import hashlib
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, FakeTools, async_test, call, reply

from govops.agent.graph import AgentContext, build_graph, run_task
from govops.audit.log import AuditWriteError, MemoryAuditLog
from govops.gateway.client import GuardrailBlocked, GuardrailUnavailable
from govops.gateway.hook_header import GuardrailInfo
from govops.tools.registry import ToolOutcome

INFO = GuardrailInfo("a" * 12, "b" * 12, "platform", ms=3)
USER = "demo-platform-g3"
REQUEST = "합성 차단 요청 SENTINEL"


async def run(chat, audit, tools=None):
    tools = tools or FakeTools()
    graph = build_graph(chat, tools, InMemorySaver(), audit=audit)
    state = await run_task(graph, task_id=uuid4().hex, user_id=USER, request=REQUEST)
    return graph, state, tools


@pytest.mark.parametrize("code", ["injection_direct", "pii_input"])
@async_test
async def test_blocked_input_is_terminal_and_content_free(code, monkeypatch):
    attrs = []
    monkeypatch.setattr(
        "govops.agent.graph.set_attributes", lambda **kw: attrs.append(kw)
    )
    audit = MemoryAuditLog()
    chat = FakeChat(GuardrailBlocked(code, replace(INFO, pre="block", c=code)))
    _, state, tools = await run(chat, audit)
    assert state["status"] == "blocked_input"
    assert state["error"] == code and state["guardrail_block"] == {"code": code}
    assert state["answer"] is None and state["steps"] == len(chat.calls) == 1
    assert len(state["messages"]) == 2
    assert tools.calls == tools.effects == []
    assert len(audit.records) == 1
    row = audit.records[0]
    assert row["event"] == "guardrail.input_blocked" and row["code"] == code
    assert row["rules_digest"] == INFO.rules and row["code_digest"] == INFO.code
    if code == "injection_direct":
        assert row["input_sha256"] == hashlib.sha256(REQUEST.encode()).hexdigest()
    else:
        assert "input_sha256" not in row
    assert REQUEST not in json.dumps([attrs, audit.records])
    safe = next(a for a in attrs if "guardrail.pre" in a)
    assert safe == {
        f"guardrail.{k}": getattr(replace(INFO, pre="block"), k)
        for k in ("pre", "q", "m", "ms", "team", "rules", "code")
    }


@pytest.mark.parametrize(
    "code",
    [
        "guardrail_unavailable",
        "team_unknown",
        "guardrail_header_missing",
        "guardrail_stale",
    ],
)
@async_test
async def test_hook_failure_is_failed(code):
    audit = MemoryAuditLog()
    _, state, tools = await run(FakeChat(GuardrailUnavailable(code)), audit)
    assert state["status"] == "failed" and state["error"] == code
    assert state["guardrail_block"] is None and tools.calls == []
    assert [r["event"] for r in audit.records] == ["guardrail.error"]
    assert audit.records[0]["code"] == code


@pytest.mark.parametrize("ids", [("call_one",), ("call_one", "toolu_two")])
@async_test
async def test_unique_quarantine_and_maxima_across_calls(ids, monkeypatch):
    attrs = []
    monkeypatch.setattr(
        "govops.agent.graph.set_attributes", lambda **kw: attrs.append(kw)
    )
    info = replace(INFO, ids=ids, q=len(ids) + 3, bad=3, m=4, trunc=1)
    audit = MemoryAuditLog()
    chat = FakeChat(
        replace(reply(calls=[call()]), guardrail=info),
        replace(reply(calls=[call()]), guardrail=replace(info, m=6, bad=2, trunc=0)),
        replace(reply("답변"), guardrail=replace(info, trunc=0)),
    )
    _, state, _ = await run(
        chat,
        audit,
        FakeTools(
            ToolOutcome({"text": "TOOL-BODY-SENTINEL"}, False, ()),
            ToolOutcome({}, False, ()),
        ),
    )
    assert state["quarantined_ids"] == [
        hashlib.sha256(i.encode()).hexdigest() for i in info.ids
    ]
    assert state["quarantine_unmatched"] == 3 and state["masked_total"] == 6
    assert [
        r["count"] for r in audit.records if r["event"] == "guardrail.tool_masked"
    ] == [4, 2]
    assert len(
        [r for r in audit.records if r["event"] == "guardrail.tool_quarantined"]
    ) == len(ids)
    assert all(
        r["count"] == 1
        for r in audit.records
        if r["event"] == "guardrail.tool_quarantined"
    )
    assert state["guardrail_truncated"] is True
    assert all(i not in json.dumps([audit.records, attrs]) for i in info.ids)
    assert "TOOL-BODY-SENTINEL" not in json.dumps(attrs)
    assert next(a for a in attrs if "guardrail.pre" in a) == {
        f"guardrail.{key}": getattr(info, key)
        for key in ("pre", "q", "m", "ms", "team", "rules", "code")
    }


@pytest.mark.parametrize(
    "post,event,code",
    [
        ("flag", "guardrail.output_flagged", None),
        ("error", "guardrail.error", "guardrail_post_error"),
    ],
)
@async_test
async def test_post_detection_is_fail_open(post, event, code):
    audit = MemoryAuditLog()
    _, state, _ = await run(
        FakeChat(replace(reply("답변"), guardrail=replace(INFO, post=post))), audit
    )
    assert state["status"] == "completed"
    rows = [r for r in audit.records if r["event"] != "model.call"]
    assert len(rows) == 1 and rows[0]["event"] == event
    assert len([r for r in audit.records if r["event"] == "model.call"]) == 1
    if code:
        assert rows[0]["code"] == code


@async_test
async def test_truncated_mixed_ids_and_legacy_checkpoint():
    audit = MemoryAuditLog()
    info = replace(
        INFO,
        q=70,
        ids=tuple(f"{'call' if i % 2 else 'toolu'}_{i}" for i in range(64)),
        trunc=1,
    )
    chat = FakeChat(replace(reply("답변"), guardrail=info))
    graph, state, _ = await run(chat, audit)
    assert len(state["quarantined_ids"]) == 64 and state["guardrail_truncated"] is True
    # Invoke a genuinely old state, without any of the new channels.
    old = {
        k: v
        for k, v in state.items()
        if k
        not in {
            "quarantined_ids",
            "quarantine_unmatched",
            "masked_total",
            "guardrail_truncated",
            "guardrail_block",
        }
    }
    old.update(task_id=uuid4().hex, status="running", steps=0)
    chat.results.append(replace(reply("답변"), guardrail=INFO))
    result = await graph.ainvoke(
        old, {"configurable": {"thread_id": old["task_id"]}}, context=AgentContext(USER)
    )
    assert result["status"] == "completed" and result["quarantined_ids"] == []


@async_test
async def test_audit_failure_preserves_defense_and_alerts(monkeypatch):
    attrs = []
    monkeypatch.setattr(
        "govops.agent.graph.set_attributes", lambda **kw: attrs.append(kw)
    )

    class Broken:
        def append(self, *args, **kwargs):
            raise AuditWriteError("offline")

    _, state, _ = await run(FakeChat(GuardrailBlocked("pii_input", INFO)), Broken())
    assert state["status"] == "blocked_input"
    assert {"guardrail.audit_gap": True} in attrs
    _, state, _ = await run(
        FakeChat(
            replace(reply("답변"), guardrail=replace(INFO, ids=("call_one",), q=1))
        ),
        Broken(),
    )
    assert state["status"] == "completed" and len(state["quarantined_ids"]) == 1
