"""The registered final answer path uses this turn's successful reads only."""

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, FakeTools, async_test, call, reply

from govops.agent import prompts
from govops.agent.graph import (
    MAX_COST_USD,
    MAX_STEPS,
    WITH_WRITES,
    AgentContext,
    EffectDeps,
    build_graph,
    run_task,
)
from govops.agent.scope import ScopeIndex
from govops.agent.turns import build_turn_state
from govops.agent.users import UserInfo
from govops.audit.log import MemoryAuditLog
from govops.costs import BudgetExceededError, LedgerMissingError
from govops.gateway.client import GatewayError, GuardrailBlocked, GuardrailUnavailable
from govops.gateway.hook_header import GuardrailInfo
from govops.guardrails.policy import TeamPolicy
from govops.guardrails.screen import screen_request
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.registry import ToolOutcome
from govops.tools.resilience import ToolPolicy

TASK = "0" * 32
USER = "demo-platform-g3"
ROOT = Path(__file__).resolve().parents[1]


def read_replies():
    return [reply(calls=[call(id=f"read-{i}")]) for i in range(MAX_STEPS)]


def read_tools(*, ok=True, ids=True):
    return FakeTools(
        *(
            ToolOutcome(
                {"doc_id": f"syn-{i}"} if ids else {},
                not ok,
                (f"syn-{i}",) if ids else (),
            )
            for i in range(MAX_STEPS)
        )
    )


async def execute(chat, tools, **kwargs):
    graph = build_graph(chat, tools, InMemorySaver(), exploration_budget=None, **kwargs)
    return await run_task(graph, task_id=TASK, user_id=USER, request="합성 조회")


def test_notice_hash_and_guardrail_input():
    digest = hashlib.sha256(prompts.FINALIZE_NOTICE.encode("utf-8")).hexdigest()
    assert digest == "f12f48773bd515212c472c54ace59093d3563419488d5bcd97b96cadf6f1b831"
    assert digest in (ROOT / "docs/results/hypotheses-m6-finalize.md").read_text()
    assert len(prompts.FINALIZE_NOTICE) == 139
    screened = screen_request(
        [
            {"role": "system", "content": prompts.SYSTEM_PROMPT},
            {"role": "user", "content": "합성 조회"},
            {"role": "user", "content": prompts.FINALIZE_NOTICE},
        ]
    )
    assert screened.verdict.action == "allow"


@async_test
async def test_permission_failures_do_not_finalize():
    chat = FakeChat(*read_replies())
    state = await execute(chat, read_tools(ok=False))
    assert state["status"] == "failed" and state["error"] == "max_steps"
    assert state["turn_read_ok"] == 0
    assert state["steps"] == len(chat.calls) == MAX_STEPS


@async_test
async def test_previous_evidence_cannot_open_final_call():
    previous = build_turn_state(
        task_id=TASK,
        user_id=USER,
        request="앞 턴",
        system_prompt="system",
        created_at="fixed",
    ) | {"evidence_ids": ["syn-previous"], "turn_read_ok": 3}
    start = build_turn_state(
        task_id="1" * 32,
        user_id=USER,
        request="새 턴",
        system_prompt="system",
        created_at="ignored",
        previous=previous,
    )
    assert start["turn_read_ok"] == 0
    assert start["evidence_ids"] == ["syn-previous"]
    chat = FakeChat(*read_replies())
    graph = build_graph(
        chat, read_tools(ok=False), InMemorySaver(), exploration_budget=None
    )
    state = await graph.ainvoke(
        start, {"configurable": {"thread_id": TASK}}, context=AgentContext(USER)
    )
    assert state["status"] == "failed" and state["error"] == "max_steps"
    assert state["turn_read_ok"] == 0 and state["evidence_ids"] == ["syn-previous"]
    assert len(chat.calls) == MAX_STEPS


@async_test
@pytest.mark.parametrize("ids", [True, False])
async def test_successful_reads_finalize_once_with_same_call_path(ids):
    audit = MemoryAuditLog()
    info = GuardrailInfo("a" * 12, "b" * 12, "platform", m=2, bad=1, trunc=1)
    chat = FakeChat(
        *read_replies(),
        replace(
            reply("[syn-0] [syn-7] [syn-unseen] 최종 답변"),
            cost_usd=0.02,
            guardrail=info,
        ),
    )
    state = await execute(chat, read_tools(ids=ids), audit=audit)
    assert state["status"] == "completed" and state["error"] is None
    assert state["stop_reason"] == "max_steps_finalized"
    assert state["steps"] == MAX_STEPS == 8
    assert state["turn_read_ok"] == 8 and len(chat.calls) == 9
    assert chat.calls[-1]["messages"][-1] == {
        "role": "user",
        "content": prompts.FINALIZE_NOTICE,
    }
    assert {k: v for k, v in chat.calls[-1].items() if k != "messages"} == {
        k: v for k, v in chat.calls[0].items() if k != "messages"
    }
    assert set(chat.calls[-1]) == {"messages", "tools", "task_id", "user_id"}
    assert state["messages"][-1] == {"role": "assistant", "content": state["answer"]}
    assert not any(
        m.get("content") == prompts.FINALIZE_NOTICE for m in state["messages"]
    )
    assert state["cited_doc_ids"] == (["syn-0", "syn-7"] if ids else [])
    assert state["turn_cost_usd"] == 0.02
    assert state["masked_total"] == 2 and state["quarantine_unmatched"] == 1
    assert state["guardrail_truncated"] is True
    calls = [r for r in audit.records if r["event"] == "model.call"]
    assert len(calls) == 9 and calls[-1]["cost_usd"] == "0.020000"
    assert all(r["task_id"] == TASK and r["actor_sub"] == USER for r in calls)


@async_test
@pytest.mark.parametrize(
    "last",
    [
        reply(),
        reply(""),
        reply(" \n\t"),
        reply("본문", [call()]),
        reply(
            "본문",
            [call("slack_send_message", '{"channel":"team-platform","text":"합성"}')],
        ),
    ],
)
async def test_invalid_final_response_has_no_tool_or_draft(last):
    user = UserInfo(USER, "platform", 3)
    tools = read_tools()
    tools.policy = ToolPolicy()
    audit = MemoryAuditLog()

    class Effects:
        async def send_message(self, *args, **kwargs):
            pytest.fail("Final call executed an effect")

        async def add_comment(self, *args, **kwargs):
            pytest.fail("Final call executed an effect")

        async def create_followup(self, *args, **kwargs):
            pytest.fail("Final call executed an effect")

    chat = FakeChat(
        *read_replies(),
        replace(
            last,
            cost_usd=0.03,
            guardrail=GuardrailInfo("a" * 12, "b" * 12, "platform", m=1),
        ),
    )
    state = await execute(
        chat,
        tools,
        profile=WITH_WRITES,
        audit=audit,
        effects=EffectDeps(Effects(), Effects(), InMemoryOutboxStore()),
        scope=ScopeIndex({}, {}, (user,)),
        policy=TeamPolicy({"platform": "block"}),
        users={USER: user},
    )
    assert state["status"] == "failed" and state["error"] == "max_steps"
    assert state["steps"] == 8 and len(chat.calls) == 9
    assert len(tools.calls) == 8 and tools.effects == []
    assert state["draft"] is None and state["draft_sha256"] is None
    assert state["effect"] is None and state["approvals"] == []
    assert state["answer"] is None and state["stop_reason"] is None
    assert state["turn_cost_usd"] == 0.03 and state["masked_total"] == 1
    assert len([r for r in audit.records if r["event"] == "model.call"]) == 9
    assert not any(r["event"].startswith(("draft.", "effect.")) for r in audit.records)


@async_test
async def test_cost_limit_at_eighth_step_preserves_max_steps_precedence():
    assert MAX_COST_USD == 0.40
    assert 7 * 0.055 < MAX_COST_USD <= 8 * 0.055
    chat = FakeChat(*(replace(r, cost_usd=0.055) for r in read_replies()))
    state = await execute(chat, read_tools())
    assert state["turn_read_ok"] == 8
    assert state["status"] == "failed" and state["error"] == "max_steps"
    assert len(chat.calls) == state["steps"] == 8
    assert state["turn_cost_usd"] == pytest.approx(0.44)


@async_test
@pytest.mark.parametrize(
    "exc,status,error",
    [
        (
            GuardrailBlocked("injection_direct", None),
            "blocked_input",
            "injection_direct",
        ),
        (
            GuardrailUnavailable("guardrail_unavailable"),
            "failed",
            "guardrail_unavailable",
        ),
        (BudgetExceededError("synthetic"), "failed", "budget_exceeded"),
        (LedgerMissingError("synthetic"), "failed", "ledger_missing"),
        (GatewayError("synthetic"), "failed", "gateway_error"),
        (RuntimeError("synthetic"), "failed", "model_error"),
    ],
)
async def test_final_call_uses_existing_exception_handling(exc, status, error):
    chat = FakeChat(*read_replies(), exc)
    state = await execute(chat, read_tools(), audit=MemoryAuditLog())
    assert state["status"] == status and state["error"] == error
    assert state["steps"] == 8 and len(chat.calls) == 9
    assert state["draft"] is None and state["stop_reason"] is None


@async_test
async def test_counter_counts_each_success_and_accepts_legacy_state():
    chat = FakeChat(reply(calls=[call(id=str(i)) for i in range(5)]), reply("답변"))
    tools = FakeTools(
        ToolOutcome({}, False, ()),
        ToolOutcome({}, True, ()),
        RuntimeError("synthetic"),
        ToolOutcome({}, False, ()),
        ToolOutcome({}, False, ()),
    )
    start = build_turn_state(
        task_id=TASK,
        user_id=USER,
        request="조회",
        system_prompt="system",
        created_at="fixed",
    )
    start.pop("turn_read_ok", None)
    graph = build_graph(chat, tools, InMemorySaver(), exploration_budget=None)
    state = await graph.ainvoke(
        start, {"configurable": {"thread_id": TASK}}, context=AgentContext(USER)
    )
    assert state["turn_read_ok"] == 3
