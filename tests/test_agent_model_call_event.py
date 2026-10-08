import asyncio
import json
import re
import threading
from dataclasses import replace
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, FakeTools, async_test, call, reply

from govops.agent.graph import build_graph, run_task
from govops.agent.users import UserInfo
from govops.audit.log import AuditLog, AuditWriteError, MemoryAuditLog
from govops.gateway.client import GatewayError, GuardrailBlocked, GuardrailUnavailable
from govops.gateway.hook_header import GuardrailInfo
from govops.tools.registry import ToolOutcome

USER = "demo-platform-g3"
REQUEST = "REQUEST-SENTINEL"
ANSWER = "ANSWER-SENTINEL"
INFO = GuardrailInfo("a" * 12, "b" * 12, "finance", q=2, m=1, post="flag")


async def run(chat, audit, *, users=None, tools=None):
    task_id = uuid4().hex
    graph = build_graph(
        chat, tools or FakeTools(), InMemorySaver(), audit=audit, users=users
    )
    state = await run_task(graph, task_id=task_id, user_id=USER, request=REQUEST)
    return task_id, state


@pytest.fixture(params=["memory", "signed"])
def sink(request, tmp_path):
    return MemoryAuditLog() if request.param == "memory" else AuditLog(tmp_path)


def records(sink):
    if isinstance(sink, MemoryAuditLog):
        return sink.records
    return [
        json.loads(line)
        for path in sorted(sink.directory.glob("chain-*.jsonl"))
        for line in path.read_text().splitlines()
    ]


@pytest.mark.parametrize(
    "group,deployment,fallback,info,users,team",
    [
        (
            "agent-primary",
            "openai-gpt-6-luna",
            False,
            INFO,
            {USER: UserInfo(USER, "platform", 3)},
            "finance",
        ),
        (
            "agent-primary",
            "anthropic-claude-sonnet-5-5",
            True,
            None,
            {USER: UserInfo(USER, "platform", 3)},
            "platform",
        ),
        ("provider/model", "INVALID/body", False, None, None, "none"),
        (None, None, False, None, {}, "none"),
        (
            "agent-" + "a" * 31,
            "a" * 65,
            False,
            None,
            {"demo-finance-g3": UserInfo("demo-finance-g3", "finance", 3)},
            "none",
        ),
    ],
)
@async_test
async def test_success_fields(sink, group, deployment, fallback, info, users, team):
    result = replace(
        reply(ANSWER),
        model="BODY-MODEL-SENTINEL",
        model_group=group,
        deployment_id=deployment,
        fallback_used=fallback,
        cost_usd=0.01234567,
        guardrail=info,
    )
    task_id, state = await run(FakeChat(result), sink, users=users)
    rows = records(sink)
    calls = [r for r in rows if r["event"] == "model.call"]
    assert len(calls) == 1
    row = calls[0]
    assert state["status"] == "completed" and state["answer"] == ANSWER
    assert row["task_id"] == task_id
    assert row.get("actor_sub", row.get("actor", {}).get("sub")) == USER
    assert row["team"] == team
    assert row["model"] == (group if group == "agent-primary" else "unknown")
    assert row["deployment_id"] == (
        deployment
        if deployment in {"openai-gpt-6-luna", "anthropic-claude-sonnet-5-5"}
        else "unknown"
    )
    assert row["fallback"] is fallback and type(row["fallback"]) is bool
    assert row["cost_usd"] == "0.012346" and re.fullmatch(
        r"\d+\.\d{6}", row["cost_usd"]
    )
    bundle = {f"guardrail_{k}" for k in ("pre", "post", "q", "m")}
    assert bundle & row.keys() == (bundle if info else set())
    if info:
        assert {k: row[k] for k in bundle} == {
            f"guardrail_{k}": getattr(info, k) for k in ("pre", "post", "q", "m")
        }
        assert rows.index(row) < next(
            i for i, r in enumerate(rows) if r["event"] == "guardrail.output_flagged"
        )
    assert all(
        s not in json.dumps(rows) for s in (REQUEST, ANSWER, "BODY-MODEL-SENTINEL")
    )


@async_test
async def test_each_loop_call_is_content_free(sink):
    arguments = '{"ticket_id":"SYN-987654"}'
    chat = FakeChat(reply(calls=[call(arguments=arguments)]), reply(ANSWER))
    task_id, state = await run(chat, sink, tools=FakeTools(ToolOutcome({}, False, ())))
    rows = records(sink)
    calls = [r for r in rows if r["event"] == "model.call"]
    assert state["status"] == "completed" and len(calls) == len(chat.calls) == 2
    assert all(
        r["task_id"] == task_id
        and r.get("actor_sub", r.get("actor", {}).get("sub")) == USER
        for r in calls
    )
    assert all(
        s not in json.dumps(rows) for s in (REQUEST, ANSWER, arguments, "SYN-987654")
    )


@pytest.mark.parametrize(
    "error,event",
    [
        (GuardrailBlocked("pii_input", INFO), "guardrail.input_blocked"),
        (GuardrailUnavailable("guardrail_unavailable"), "guardrail.error"),
        (GatewayError("safe"), None),
    ],
)
@async_test
async def test_failed_call_has_no_model_event(sink, error, event):
    _, state = await run(FakeChat(error), sink)
    assert state["status"] in {"failed", "blocked_input"}
    assert [r["event"] for r in records(sink) if r["event"] != "chain.genesis"] == (
        [event] if event else []
    )


@pytest.mark.parametrize(
    "error", [AuditWriteError("PRIVATE-SENTINEL"), RuntimeError("PRIVATE-SENTINEL")]
)
@async_test
async def test_audit_failure_is_content_free_and_fail_open(monkeypatch, error):
    attrs = []
    monkeypatch.setattr(
        "govops.agent.graph.set_attributes", lambda **kw: attrs.append(kw)
    )

    class Broken:
        def append(self, *args, **kwargs):
            raise error

    chat = FakeChat(reply(ANSWER))
    _, state = await run(chat, Broken())
    _, baseline = await run(FakeChat(reply(ANSWER)), None)
    assert (state["status"], state["answer"], state["steps"]) == (
        baseline["status"],
        baseline["answer"],
        baseline["steps"],
    )
    assert {"model.audit_gap": True} in attrs
    assert "PRIVATE-SENTINEL" not in json.dumps(attrs)


@async_test
async def test_append_does_not_block_event_loop():
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    completed = []

    class Blocking:
        def append(self, *args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            completed.append(release.wait(timeout=5))

    async def unblock():
        await asyncio.wait_for(entered.wait(), timeout=6)
        release.set()

    (_, state), _ = await asyncio.gather(
        run(FakeChat(reply(ANSWER)), Blocking()), unblock()
    )
    assert completed == [True] and state["status"] == "completed"


@async_test
async def test_read_only_without_audit_skips_record(monkeypatch):
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("unexpected audit thread")

    monkeypatch.setattr("govops.agent.graph.asyncio.to_thread", forbidden)
    _, state = await run(FakeChat(reply(ANSWER)), None)
    assert state["status"] == "completed" and state["answer"] == ANSWER
    assert attempts == []
