"""Semantic collection points with deterministic, credential-free classifiers."""

import asyncio
import copy
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, FakeTools, async_test, call, reply
from test_obs_trace import Sink

from govops.agent.graph import MAX_COST_USD, MAX_STEPS, READ_ONLY, build_graph, run_task
from govops.agent.prompts import FINALIZE_NOTICE
from govops.audit.log import AuditLog, AuditWriteError, MemoryAuditLog
from govops.costs import BudgetExceededError, LedgerMissingError
from govops.gateway.client import ClassifyResult, GuardrailBlocked
from govops.guardrails.semantic import load_config, select_tool_items, select_user_item
from govops.obs.trace import SpanKind, trace
from govops.tools.registry import ToolOutcome

USER = "demo-platform-g3"
REQUEST = "합성 운영 기록을 읽고 정리해 주세요."
TEXT = "합성 센티널 문장입니다. 운영 기록의 내용과 처리 내역을 확인하는 평범한 한국어 문장입니다."
MARKER = "[정책으로 일부 제외]"
NOW = datetime(2026, 10, 14, tzinfo=UTC)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Classifier:
    def __init__(self, *, user="safe", tool="safe", status="ok", error=None):
        self.user, self.tool, self.status, self.error = user, tool, status, error
        self.calls = []
        self.active = self.peak = 0

    async def classify(self, *, request, task_id, user_id):
        self.calls.append(copy.deepcopy((request, task_id, user_id)))
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0)
            if self.error is not None:
                raise self.error
            label = self.tool if request.tools is not None else self.user
            return ClassifyResult(label, self.status, 3, 2, 0)
        finally:
            self.active -= 1


async def execute(chat, *, tools=None, semantic=None, audit=None, previous=None):
    graph = build_graph(
        chat,
        tools or FakeTools(),
        InMemorySaver(),
        semantic=semantic,
        audit=audit,
        clock=lambda: NOW,
        exploration_budget=4,
    )
    state = await run_task(
        graph,
        task_id="a" * 32,
        user_id=USER,
        request=REQUEST,
        previous=previous,
    )
    return graph, state


def tool_calls(classifier):
    return [request for request, _, _ in classifier.calls if request.tools is not None]


@async_test
async def test_off_preserves_exact_messages_arguments_checkpoint_and_audit(monkeypatch):
    def forbidden():
        pytest.fail("off must not load classifier configuration")

    monkeypatch.setattr("govops.guardrails.semantic.load_config", forbidden)
    states, calls, records, traces = [], [], [], []
    for explicit in (False, True):
        chat = FakeChat(reply(calls=[call(id="call_one")]), reply("합성 답변"))
        payload = {"text": TEXT}
        audit, sink = MemoryAuditLog(), Sink()
        graph = build_graph(
            chat,
            FakeTools(ToolOutcome(payload, False, ())),
            InMemorySaver(),
            audit=audit,
            clock=lambda: NOW,
            **({"semantic": None} if explicit else {}),
        )
        with trace("a" * 32, sinks=[sink]):
            state = await run_task(
                graph, task_id="a" * 32, user_id=USER, request=REQUEST
            )
        assert state["quarantined_ids"] == []
        assert (
            await graph.aget_state({"configurable": {"thread_id": "a" * 32}})
        ).values == state
        states.append(json.dumps(state, ensure_ascii=False).encode())
        calls.append(json.dumps(chat.calls, ensure_ascii=False).encode())
        records.append(audit.records)
        traces.append([(r.kind, r.name, r.attributes) for r in sink.records])
    assert states[0] == states[1] and calls[0] == calls[1]
    assert records[0] == records[1] and traces[0] == traces[1]
    assert not any(kind == SpanKind.GUARDRAIL for kind, _, _ in traces[0])
    initial = [
        {"role": "system", "content": READ_ONLY.system_prompt},
        {"role": "user", "content": REQUEST},
    ]
    assistant = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_one",
                "type": "function",
                "function": {
                    "name": "jira_get_ticket",
                    "arguments": '{"ticket_id":"SYN-1"}',
                },
            }
        ],
    }
    expected = [
        {
            "messages": initial,
            "tools": READ_ONLY.tools,
            "task_id": "a" * 32,
            "user_id": USER,
        },
        {
            "messages": [
                *initial,
                assistant,
                {
                    "role": "tool",
                    "tool_call_id": "call_one",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            "tools": READ_ONLY.tools,
            "task_id": "a" * 32,
            "user_id": USER,
        },
    ]
    assert calls[0] == json.dumps(expected, ensure_ascii=False).encode()
    assert (
        records[0]
        == [
            {
                "event": "model.call",
                "task_id": "a" * 32,
                "actor_sub": USER,
                "team": "none",
                "model": "unknown",
                "deployment_id": "unknown",
                "fallback": False,
                "cost_usd": "0.000000",
            }
        ]
        * 2
    )


@async_test
async def test_user_unsafe_blocks_before_steps_and_matches_gateway_audit():
    semantic, audit, chat = Classifier(user="unsafe"), MemoryAuditLog(), FakeChat()
    assert select_user_item(REQUEST, load_config()) is not None
    graph, state = await execute(chat, semantic=semantic, audit=audit)
    assert graph.agent_semantic is semantic
    assert len(semantic.calls) == 1 and semantic.calls[0][1:] == ("a" * 32, USER)
    assert chat.calls == [] and state["steps"] == 0
    assert state["status"] == "blocked_input" and state["error"] == "injection_direct"
    assert state["answer"] is None and state["guardrail_block"] == {
        "code": "injection_direct"
    }
    legacy = MemoryAuditLog()
    await execute(FakeChat(GuardrailBlocked("injection_direct", None)), audit=legacy)
    assert audit.records == [legacy.records[0] | {"v2": True}]
    assert audit.records[0]["input_sha256"] == digest(REQUEST)


@async_test
async def test_tool_quarantine_reaches_next_model_and_is_not_reclassified():
    semantic, audit = Classifier(tool="unsafe"), MemoryAuditLog()
    chat = FakeChat(
        reply(calls=[call(id="call_one")]),
        reply(calls=[call(id="call_one")]),
        reply("답변"),
    )
    _, state = await execute(
        chat,
        semantic=semantic,
        audit=audit,
        tools=FakeTools(
            ToolOutcome({"text": TEXT}, False, ()),
            ToolOutcome({"text": MARKER}, False, ()),
        ),
    )
    assert len(tool_calls(semantic)) == 1
    assert len(semantic.calls) == 2
    assert TEXT not in json.dumps(chat.calls[1:], ensure_ascii=False)
    assert json.loads(chat.calls[1]["messages"][-1]["content"]) == {"text": MARKER}
    assert state["quarantined_ids"] == [digest("call_one")]
    assert [r for r in audit.records if r["event"] == "guardrail.tool_quarantined"] == [
        {
            "event": "guardrail.tool_quarantined",
            "task_id": "a" * 32,
            "actor_sub": USER,
            "tool_call_sha256": digest("call_one"),
            "count": 1,
            "v2": True,
        }
    ]


@async_test
async def test_repeated_identifier_is_audited_once():
    semantic, audit = Classifier(tool="unsafe"), MemoryAuditLog()
    _, state = await execute(
        FakeChat(
            reply(calls=[call(id="call_one"), call(id="call_one")]), reply("답변")
        ),
        semantic=semantic,
        audit=audit,
        tools=FakeTools(*(ToolOutcome({"text": TEXT}, False, ()) for _ in range(2))),
    )
    assert len(tool_calls(semantic)) == 2
    assert state["quarantined_ids"] == [digest("call_one")]
    assert (
        len([r for r in audit.records if r["event"] == "guardrail.tool_quarantined"])
        == 1
    )


@pytest.mark.parametrize(
    "status", ["timeout", "rate_limited", "error", "parse", "v1_blocked"]
)
@async_test
async def test_unclassified_user_and_tool_pass_and_alert_once_each(status):
    semantic, audit = (
        Classifier(status=status, user="unsafe", tool="unsafe"),
        MemoryAuditLog(),
    )
    candidates, excess = select_tool_items(
        [
            {
                "role": "tool",
                "tool_call_id": "call_one",
                "content": json.dumps({"text": TEXT}, ensure_ascii=False),
            }
        ],
        load_config(),
    )
    assert len(candidates) == 1 and excess == 0
    assert select_user_item(REQUEST, load_config()) is not None
    _, state = await execute(
        FakeChat(reply(calls=[call(id="call_one")]), reply("답변")),
        semantic=semantic,
        audit=audit,
        tools=FakeTools(ToolOutcome({"text": TEXT}, False, ())),
    )
    assert len(semantic.calls) == 2
    assert state["status"] == "completed" and state["guardrail_block"] is None
    assert state["quarantined_ids"] == []
    assert [r for r in audit.records if r["event"].startswith("guardrail.")] == [
        {
            "event": "guardrail.error",
            "task_id": "a" * 32,
            "actor_sub": USER,
            "code": "guardrail_v2_error",
            "count": 1,
        }
    ] * 2


@pytest.mark.parametrize(
    "error,code",
    [(BudgetExceededError, "budget_exceeded"), (LedgerMissingError, "ledger_missing")],
)
@async_test
async def test_classifier_budget_failure_stops_before_model(error, code):
    semantic, chat = Classifier(error=error("합成 오류")), FakeChat()
    _, state = await execute(chat, semantic=semantic)
    assert len(semantic.calls) == 1 and chat.calls == []
    assert (
        state["status"] == "failed" and state["error"] == code and state["steps"] == 0
    )


@pytest.mark.parametrize(
    "error,code",
    [(BudgetExceededError, "budget_exceeded"), (LedgerMissingError, "ledger_missing")],
)
@async_test
async def test_tool_classifier_budget_failure_stops_next_model(error, code):
    class ToolFailure(Classifier):
        async def classify(self, **kwargs):
            if kwargs["request"].tools is not None:
                self.error = error("합성 오류")
            return await super().classify(**kwargs)

    semantic, chat = ToolFailure(), FakeChat(reply(calls=[call(id="call_one")]))
    _, state = await execute(
        chat, semantic=semantic, tools=FakeTools(ToolOutcome({"text": TEXT}, False, ()))
    )
    assert len(semantic.calls) == 2 and len(chat.calls) == 1
    assert (
        state["status"] == "failed" and state["error"] == code and state["steps"] == 1
    )


@async_test
async def test_quarantine_preserves_evidence_observed_and_exploration():
    values = []
    payload = {"doc_id": "syn-semantic", "text": TEXT}
    for semantic in (None, Classifier(tool="unsafe")):
        _, state = await execute(
            FakeChat(
                reply(
                    calls=[
                        call("get_document", '{"doc_id":"syn-semantic"}', id="call_one")
                    ]
                ),
                reply(
                    calls=[
                        call("get_document", '{"doc_id":"syn-semantic"}', id="call_two")
                    ]
                ),
                reply("답변"),
            ),
            semantic=semantic,
            tools=FakeTools(
                *(ToolOutcome(payload, False, ("syn-semantic",)) for _ in range(2))
            ),
        )
        values.append(
            {
                k: state[k]
                for k in (
                    "evidence_ids",
                    "observed_ids",
                    "explored_ids",
                    "no_new_info_streak",
                    "turn_read_ok",
                )
            }
        )
    assert (
        values
        == [
            {
                "evidence_ids": ["syn-semantic"],
                "observed_ids": ["syn-semantic"],
                "explored_ids": ["syn-semantic"],
                "no_new_info_streak": 1,
                "turn_read_ok": 2,
            }
        ]
        * 2
    )


@async_test
async def test_finalize_history_and_generated_errors_are_not_classified():
    from govops.agent.graph import AgentContext
    from govops.agent.turns import build_turn_state

    semantic, chat = Classifier(), FakeChat(reply("마무리 답변"))
    graph = build_graph(chat, FakeTools(), InMemorySaver(), semantic=semantic)
    state = build_turn_state(
        task_id="a" * 32,
        user_id=USER,
        request=REQUEST,
        system_prompt="합성",
        created_at=NOW.isoformat(),
    )
    state.update(steps=MAX_STEPS, turn_read_ok=1)
    result = await graph.ainvoke(
        state, {"configurable": {"thread_id": "a" * 32}}, context=AgentContext(USER)
    )
    assert result["status"] == "completed" and semantic.calls == []
    assert chat.calls[0]["messages"][-1]["content"] == FINALIZE_NOTICE
    previous = state | {
        "steps": 1,
        "status": "completed",
        "request": TEXT,
        "answer": TEXT,
    }
    semantic = Classifier()
    await execute(
        FakeChat(
            reply(calls=[call(id="call_one", arguments="invalid")]), reply("답변")
        ),
        semantic=semantic,
        previous=previous,
    )
    assert len(semantic.calls) == 1 and not tool_calls(semantic)
    assert semantic.calls[0][0].messages[-1]["content"] == REQUEST


@async_test
async def test_cost_limit_precedes_classification():
    from govops.agent.graph import AgentContext
    from govops.agent.turns import build_turn_state

    semantic, chat = Classifier(), FakeChat()
    graph = build_graph(chat, FakeTools(), InMemorySaver(), semantic=semantic)
    state = build_turn_state(
        task_id="a" * 32,
        user_id=USER,
        request=REQUEST,
        system_prompt="합성",
        created_at=NOW.isoformat(),
    )
    state["turn_cost_usd"] = MAX_COST_USD
    result = await graph.ainvoke(
        state, {"configurable": {"thread_id": "a" * 32}}, context=AgentContext(USER)
    )
    assert result["error"] == "max_cost" and semantic.calls == chat.calls == []


@async_test
async def test_v1_blocked_input_skips_classifier_and_keeps_gateway_authority():
    from govops.guardrails.screen import screen_user_messages

    request = "합성 연락처 010-2345-6789"
    verdict = screen_user_messages([{"role": "user", "content": request}])
    assert (
        verdict.action == "block" and select_user_item(request, load_config()) is None
    )
    semantic, chat = Classifier(), FakeChat(GuardrailBlocked(verdict.code, None))
    graph = build_graph(chat, FakeTools(), InMemorySaver(), semantic=semantic)
    state = await run_task(graph, task_id="a" * 32, user_id=USER, request=request)
    assert semantic.calls == [] and len(chat.calls) == 1
    assert state["status"] == "blocked_input" and state["error"] == verdict.code


@async_test
async def test_round_limit_failures_and_concurrency_are_aggregated():
    semantic, audit, sink = Classifier(status="timeout"), MemoryAuditLog(), Sink()
    payload = {"paragraphs": [TEXT + chr(0xAC00 + i) for i in range(19)]}
    config = load_config()
    candidates, excess = select_tool_items(
        [
            {
                "role": "tool",
                "tool_call_id": "call_one",
                "content": json.dumps(payload, ensure_ascii=False),
            }
        ],
        config,
    )
    assert len(candidates) == 16 and excess == 3
    with trace("a" * 32, sinks=[sink]):
        _, state = await execute(
            FakeChat(reply(calls=[call(id="call_one")]), reply("답변")),
            semantic=semantic,
            audit=audit,
            tools=FakeTools(ToolOutcome(payload, False, ())),
        )
    assert state["status"] == "completed"
    assert len(tool_calls(semantic)) == 16 and semantic.peak == config["concurrency"]
    alerts = [r for r in audit.records if r["event"] == "guardrail.error"]
    assert [r["count"] for r in alerts] == [1, 19]
    assert all(r["code"] == "guardrail_v2_error" for r in alerts)
    spans = [r for r in sink.records if r.kind == SpanKind.GUARDRAIL]
    assert len(spans) == 2
    for record, n in zip(spans, (1, 16), strict=True):
        assert set(record.attributes) == {
            f"guardrail.v2_{k}" for k in ("n", "ok", "flag", "fail", "in", "out", "ms")
        }
        assert record.attributes == {
            "guardrail.v2_n": n,
            "guardrail.v2_ok": 0,
            "guardrail.v2_flag": 0,
            "guardrail.v2_fail": n,
            "guardrail.v2_in": 3 * n,
            "guardrail.v2_out": 2 * n,
            "guardrail.v2_ms": record.attributes["guardrail.v2_ms"],
        }
        assert record.attributes["guardrail.v2_ms"] >= 0
    assert TEXT not in json.dumps(
        [asdict(r) for r in sink.records] + audit.records, ensure_ascii=False
    )


@pytest.mark.parametrize("unsafe_user", [False, True])
@async_test
async def test_audit_failure_preserves_semantic_defense(unsafe_user):
    class Broken:
        def append(self, *args, **kwargs):
            raise AuditWriteError("합성 오류")

    semantic, sink = (
        Classifier(user="unsafe" if unsafe_user else "safe", tool="unsafe"),
        Sink(),
    )
    chat = (
        FakeChat()
        if unsafe_user
        else FakeChat(reply(calls=[call(id="call_one")]), reply("답변"))
    )
    with trace("a" * 32, sinks=[sink]):
        _, state = await execute(
            chat,
            semantic=semantic,
            audit=Broken(),
            tools=FakeTools(ToolOutcome({"text": TEXT}, False, ())),
        )
    assert state["status"] == ("blocked_input" if unsafe_user else "completed")
    assert state["quarantined_ids"] == ([] if unsafe_user else [digest("call_one")])
    if not unsafe_user:
        assert json.loads(chat.calls[-1]["messages"][-1]["content"])["text"] == MARKER
    assert any(r.attributes.get("guardrail.audit_gap") is True for r in sink.records)


@pytest.mark.parametrize("sink_type", [AuditLog, MemoryAuditLog])
def test_v2_audit_fields_use_existing_validation(tmp_path, sink_type):
    audit = sink_type(tmp_path) if sink_type is AuditLog else sink_type()
    for event, fields in [
        (
            "guardrail.input_blocked",
            {"code": "injection_direct", "input_sha256": digest(REQUEST), "v2": True},
        ),
        (
            "guardrail.tool_quarantined",
            {"tool_call_sha256": digest("call_one"), "count": 1, "v2": True},
        ),
        ("guardrail.error", {"code": "guardrail_v2_error", "count": 2}),
    ]:
        audit.append(event, task_id=uuid4().hex, actor_sub=USER, **fields)
    records = (
        audit.records
        if sink_type is MemoryAuditLog
        else [
            json.loads(line)
            for path in tmp_path.glob("chain-*.jsonl")
            for line in path.read_text().splitlines()
        ][1:]
    )
    assert len(records) == 3
    assert records[0]["v2"] is True and records[1]["v2"] is True
    assert records[2]["code"] == "guardrail_v2_error" and records[2]["count"] == 2
