"""Turn isolation and locally measured LangGraph checkpoint contracts."""

from typing import TypedDict
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from test_agent_graph import FakeChat, FakeTools, async_test, call, reply

from govops.agent.graph import AgentState, build_graph, get_task, run_task
from govops.agent.turns import (
    CARRY_STATUSES,
    CONVERSATION_KEYS,
    MAX_TURNS,
    TURN_KEYS,
    TurnLimitError,
    build_turn_state,
    next_history,
    turn_defaults,
)
from govops.gateway.client import GatewayError, GuardrailBlocked
from govops.tools.registry import ToolOutcome


def initial(**kwargs):
    # Fixed timestamp tests preservation, rather than wall-clock timing behavior.
    return build_turn_state(
        task_id="turn-1",
        user_id="owner",
        request="첫 요청",
        system_prompt="system",
        created_at="2026-10-08T00:00:00+00:00",
        **kwargs,
    )


def test_state_key_partition_and_legacy_defaults():
    assert not CONVERSATION_KEYS & TURN_KEYS
    assert CONVERSATION_KEYS | TURN_KEYS == set(AgentState.__annotations__)
    legacy = {
        "task_id": "turn-1",
        "user_id": "owner",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "첫 요청"},
        ],
        "steps": 0,
        "status": "running",
        "quarantined_ids": [],
        "quarantine_unmatched": 0,
        "masked_total": 0,
        "guardrail_truncated": False,
        "guardrail_block": None,
        "answer": None,
        "error": None,
        "observed_ids": [],
        "cited_doc_ids": [],
        "evidence_ids": [],
        "draft": None,
        "draft_sha256": None,
        "check": None,
        "approvals": [],
        "awaiting_since": None,
        "expires_at": None,
        "effect": None,
        "created_at": "2026-10-08T00:00:00+00:00",
        "resume_value": None,
        "edit_approver": None,
    }
    assert len(legacy) == 25
    state = initial()
    assert {key: state[key] for key in legacy} == legacy
    assert state["conversation_id"] == "turn-1"
    assert state["history"] == [] and state["turn_index"] == 0
    assert set(turn_defaults()) == TURN_KEYS
    assert {
        key: state[key]
        for key in (
            "approval_receipt",
            "edit_original",
            "rejected_edit",
            "effect_result",
            "effect_at",
            "turn_cost_usd",
            "explored_ids",
            "no_new_info_streak",
            "stop_reason",
        )
    } == {
        "approval_receipt": None,
        "edit_original": None,
        "rejected_edit": None,
        "effect_result": None,
        "effect_at": None,
        "turn_cost_usd": 0.0,
        "explored_ids": [],
        "no_new_info_streak": 0,
        "stop_reason": None,
    }
    assert next_history({}) == []
    assert MAX_TURNS == 10
    assert CARRY_STATUSES == frozenset({"completed", "rejected", "expired", "blocked"})


@pytest.mark.parametrize(
    "status",
    [
        "completed",
        "rejected",
        "expired",
        "blocked",
        "blocked_input",
        "failed",
        "interrupted",
        "running",
        "awaiting_approval",
    ],
)
def test_next_history(status):
    history = [{"role": "user", "content": "earlier"}]
    previous = {
        "history": history,
        "request": "요청",
        "answer": "답변",
        "status": status,
    }
    expected = history + (
        [{"role": "user", "content": "요청"}, {"role": "assistant", "content": "답변"}]
        if status in CARRY_STATUSES
        else []
    )
    assert next_history(previous) == expected
    assert history == [{"role": "user", "content": "earlier"}]
    for key in ("answer", "request"):
        assert next_history(previous | {key: ""}) == history
        assert next_history(previous | {key: None}) == history


@pytest.mark.parametrize(
    "status", ["completed", "failed", "interrupted", "blocked_input"]
)
def test_turn_reset_and_conversation_preservation(status):
    previous = initial()
    previous.update(dict.fromkeys(TURN_KEYS, "polluted"))
    previous.update(
        status=status,
        task_id="old",
        request="old request",
        answer="old answer",
        evidence_ids=["syn-z", "syn-a"],
        history=[],
    )
    state = build_turn_state(
        task_id="new",
        user_id="owner",
        request="new request",
        system_prompt="new system",
        created_at="ignored",
        previous=previous,
    )
    assert {key: state[key] for key in TURN_KEYS} == turn_defaults() | {
        "task_id": "new",
        "request": "new request",
    }
    assert state["created_at"] == previous["created_at"]
    assert state["conversation_id"] == previous["conversation_id"]
    assert state["evidence_ids"] == ["syn-a", "syn-z"]
    assert state["turn_index"] == 1
    assert all(m["role"] != "tool" and "tool_calls" not in m for m in state["messages"])
    assert state["messages"] == [
        {"role": "system", "content": "new system"},
        *next_history(previous),
        {"role": "user", "content": "new request"},
    ]
    state["approvals"].append({})
    assert turn_defaults()["approvals"] == []


def test_owner_limit_and_legacy_conversation_fallback():
    with pytest.raises(ValueError, match="owner_mismatch"):
        initial(previous=initial() | {"user_id": "other"})
    with pytest.raises(TurnLimitError):
        initial(previous=initial() | {"turn_index": 9})
    previous = initial()
    del previous["conversation_id"]
    del previous["turn_index"]
    assert initial(previous=previous)["conversation_id"] == "turn-1"
    assert (
        initial(previous=previous, conversation_id="conversation")["conversation_id"]
        == "conversation"
    )
    assert initial(conversation_id="conversation")["conversation_id"] == "conversation"


@async_test
@pytest.mark.parametrize("ending", ["completed", "failed", "blocked_input"])
async def test_read_turns_preserve_evidence_without_tool_history(ending):
    ending_reply = (
        reply("[syn-turn-doc] 답변") if ending == "completed" else GatewayError("error")
    )
    chat = FakeChat(
        reply(calls=[call("get_document", '{"doc_id":"syn-turn-doc"}')]),
        ending_reply,
        reply("다음 답변"),
    )
    if ending == "blocked_input":
        chat = FakeChat(GuardrailBlocked("injection_direct", None), reply("다음 답변"))
    tools = FakeTools(
        ToolOutcome(
            {"doc_id": "syn-turn-doc", "text": "합성 본문"}, False, ("syn-turn-doc",)
        )
    )
    graph = build_graph(chat, tools, InMemorySaver())
    first = await run_task(
        graph, task_id="conversation", user_id="owner", request="첫 요청"
    )
    assert first["status"] == ending
    await run_task(
        graph,
        task_id="turn-2",
        conversation_id="conversation",
        user_id="owner",
        request="두 번째 요청",
        previous=first,
    )
    start_messages = chat.calls[-1]["messages"]
    assert all(m["role"] != "tool" and "tool_calls" not in m for m in start_messages)
    assert ({"role": "user", "content": "첫 요청"} in start_messages) == (
        ending == "completed"
    )
    state = await get_task(graph, "conversation")
    assert state["task_id"] == "turn-2" and state["observed_ids"] == []
    assert state["evidence_ids"] == (
        [] if ending == "blocked_input" else ["syn-turn-doc"]
    )
    assert await get_task(graph, "turn-2") is None


@async_test
async def test_write_second_turn_uses_first_turn_scope(tmp_path):
    from govops.agent.draft import Draft
    from govops.agent.graph import WITH_WRITES, EffectDeps
    from govops.agent.scope import AccessRow, ScopeIndex, run_checks
    from govops.agent.users import UserInfo
    from govops.audit.log import AuditLog
    from govops.guardrails.policy import TeamPolicy
    from govops.tools.outbox import InMemoryOutboxStore
    from govops.tools.resilience import ToolPolicy

    user = UserInfo("demo-platform-g3", "platform", 3)
    scope = ScopeIndex(
        {"syn-turn-doc": AccessRow("team", ("platform",), 0)},
        {},
        (user, UserInfo("demo-finance-g1", "finance", 1)),
    )
    policy = TeamPolicy({"platform": "block"})
    draft = Draft("slack_send_message", {"channel": "announce", "text": "합성 안내"})
    assert (
        run_checks(
            draft, evidence=[], requester=user, scope=scope, policy=policy
        ).status
        == "passed"
    )
    chat = FakeChat(
        reply(calls=[call("get_document", '{"doc_id":"syn-turn-doc"}')]),
        reply("근거를 읽었습니다"),
        reply(
            calls=[
                call("slack_send_message", '{"channel":"announce","text":"합성 안내"}')
            ]
        ),
    )
    tools = FakeTools(
        ToolOutcome(
            {"doc_id": "syn-turn-doc", "text": "합성"}, False, ("syn-turn-doc",)
        )
    )
    tools.policy = ToolPolicy()
    graph = build_graph(
        chat,
        tools,
        InMemorySaver(),
        profile=WITH_WRITES,
        effects=EffectDeps(None, None, InMemoryOutboxStore()),
        audit=AuditLog(tmp_path),
        scope=scope,
        policy=policy,
        users={user.label: user},
    )
    conversation_id, turn_id = uuid4().hex, uuid4().hex
    first = await run_task(
        graph, task_id=conversation_id, user_id=user.label, request="조회"
    )
    # Simulate persisted turn-only data; no pending interrupt is created.
    pollution = {
        "draft": {"old": True},
        "approvals": [{"old": True}],
        "effect": {"old": True},
        "approval_receipt": {"old": True},
    }
    await graph.aupdate_state(
        {"configurable": {"thread_id": conversation_id}}, pollution
    )
    previous = first | pollution
    start = build_turn_state(
        task_id=turn_id,
        user_id=user.label,
        request="공지 발송",
        system_prompt=WITH_WRITES.system_prompt,
        created_at="ignored",
        previous=previous,
    )
    assert start["draft"] is None and start["approvals"] == []
    assert start["effect"] is None and start["approval_receipt"] is None
    second = await run_task(
        graph,
        task_id=turn_id,
        conversation_id=conversation_id,
        user_id=user.label,
        request="공지 발송",
        previous=previous,
    )
    assert second["check"] == {
        "status": "blocked",
        "reason": "scope_wider_than_evidence",
    }
    assert (
        second["approvals"] == []
        and second["effect"] is None
        and second["approval_receipt"] is None
    )


class ContractState(TypedDict):
    value: int
    retained: str


@async_test
async def test_contract_completed_thread_new_run_retains_omitted_keys():
    calls = []

    def node(state):
        calls.append(state["value"])
        return {"value": state["value"] + 1}

    builder = StateGraph(ContractState)
    builder.add_node("model", node)
    builder.add_edge(START, "model")
    builder.add_edge("model", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "contract"}}
    first = await graph.ainvoke({"value": 1, "retained": "old"}, config)
    second = await graph.ainvoke({"value": 10}, config)
    # Success: both invocations return. Content: START reruns, omitted channel survives.
    assert first == {"value": 2, "retained": "old"}
    assert second == {"value": 11, "retained": "old"} and calls == [1, 10]
    await graph.aupdate_state(config, {"value": 20}, as_node="__start__")
    assert (await graph.aget_state(config)).next == ("model",)
    assert (await graph.ainvoke({"value": 30}, config))["value"] == 31
    assert calls == [1, 10, 30]


@async_test
async def test_contract_new_input_silently_discards_pending_interrupt():
    from langgraph.types import Command

    def approval(state):
        if state["value"] == 1:
            interrupt("approval")
        return {"value": state["value"]}

    builder = StateGraph(ContractState)
    builder.add_node("approval", approval)
    builder.add_edge(START, "approval")
    builder.add_edge("approval", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "contract"}}
    first = await graph.ainvoke({"value": 1, "retained": "old"}, config)
    assert first["__interrupt__"] and (await graph.aget_state(config)).next == (
        "approval",
    )
    second = await graph.ainvoke({"value": 2}, config)
    # Success: no exception. Content: pending approval disappears, later resume does nothing.
    assert second == {"value": 2, "retained": "old"}
    assert (await graph.aget_state(config)).next == ()
    assert await graph.ainvoke(Command(resume="approve"), config) == second


@async_test
async def test_run_task_preserves_pending_approval():
    def approval(state):
        interrupt("approval")
        return {}

    builder = StateGraph(AgentState)
    builder.add_node("approval", approval)
    builder.add_edge(START, "approval")
    builder.add_edge("approval", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    await run_task(graph, task_id="conversation", user_id="owner", request="승인 요청")
    config = {"configurable": {"thread_id": "conversation"}}
    before = await graph.aget_state(config)
    with pytest.raises(ValueError, match="approval_pending"):
        await run_task(
            graph,
            task_id="turn-2",
            user_id="owner",
            request="새 요청",
            conversation_id="conversation",
            previous=before.values,
        )
    after = await graph.aget_state(config)
    assert after.values == before.values
    assert after.next == before.next == ("approval",)
    assert after.tasks == before.tasks


@async_test
async def test_failed_then_blocked_input_keeps_failed_evidence():
    chat = FakeChat(
        reply(calls=[call("get_document", '{"doc_id":"syn-turn-doc"}')]),
        GatewayError("model_error"),
        GuardrailBlocked("injection_direct", None),
        reply("최종 답변"),
    )
    graph = build_graph(
        chat,
        FakeTools(
            ToolOutcome(
                {"doc_id": "syn-turn-doc", "text": "합성"}, False, ("syn-turn-doc",)
            )
        ),
        InMemorySaver(),
    )
    previous = None
    for index, (request, status) in enumerate(
        [
            ("실패 턴 요청", "failed"),
            ("차단 턴 요청", "blocked_input"),
            ("새 요청", "completed"),
        ]
    ):
        previous = await run_task(
            graph,
            task_id=f"turn-{index}",
            conversation_id="conversation",
            user_id="owner",
            request=request,
            previous=previous,
        )
        assert previous["status"] == status
        assert previous["evidence_ids"] == ["syn-turn-doc"]
    assert previous["history"] == []
    assert chat.calls[-1]["messages"][-1] == {"role": "user", "content": "새 요청"}
    assert all(
        m.get("content") not in {"실패 턴 요청", "차단 턴 요청"}
        for m in chat.calls[-1]["messages"]
    )
