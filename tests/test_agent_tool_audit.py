import asyncio
import hashlib
import json
import secrets
import threading
from datetime import UTC, datetime

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, FakeTools, async_test, call, reply

from govops.agent.draft import Draft, draft_sha256
from govops.agent.graph import (
    MAX_STEPS,
    PROFILES,
    WITH_WRITES,
    AgentContext,
    EffectDeps,
    build_graph,
    run_task,
)
from govops.agent.scope import AccessRow, ScopeIndex
from govops.agent.turns import build_turn_state
from govops.agent.users import UserInfo
from govops.audit.argmac import load_args_mac
from govops.audit.log import TOOL_CALL_NAMES, AuditLog, AuditWriteError, MemoryAuditLog
from govops.audit.toolcall import ToolCallAuditor
from govops.guardrails.policy import TeamPolicy
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.registry import ToolNotExecutable, ToolOutcome
from govops.tools.resilience import ToolPolicy

USER = "demo-platform-g3"
TASK = "a" * 32
NOW = datetime(2026, 10, 21, tzinfo=UTC)
ARGUMENTS = '{"ticket_id":"SYN-987654"}'
BODY = "SYNTHETIC-RESULT-BODY-SENTINEL"
WRITE_ARGS = '{"channel":"team-platform","text":"합성 안내"}'


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setenv("AUDIT_ARGS_HMAC_KEY", secrets.token_hex(32))
    return load_args_mac()


@pytest.fixture
def audit():
    return MemoryAuditLog()


@pytest.fixture
def auditor(audit, mac):
    return ToolCallAuditor(audit, mac)


def assemble(chat, tools, audit, tool_audit, *, writes=False):
    options = {}
    if writes:
        user = UserInfo(USER, "platform", 3)
        tools.policy = ToolPolicy()
        options = {
            "profile": WITH_WRITES,
            "effects": EffectDeps(object(), object(), InMemoryOutboxStore()),
            "scope": ScopeIndex(
                {},
                {"SYN-1": AccessRow("restricted", ("platform",), 3)},
                (user, UserInfo("demo-platform-g1", "platform", 1)),
            ),
            "policy": TeamPolicy({"platform": "block"}),
            "users": {USER: user},
        }
    return build_graph(
        chat,
        tools,
        InMemorySaver(),
        audit=audit,
        tool_audit=tool_audit,
        clock=lambda: NOW,
        exploration_budget=None,
        **options,
    )


async def run(chat, tools, audit, tool_audit, *, writes=False):
    graph = assemble(chat, tools, audit, tool_audit, writes=writes)
    state = await run_task(graph, task_id=TASK, user_id=USER, request="합성 조회 요청")
    return graph, state


def scenario(path):
    write = call("slack_send_message", WRITE_ARGS, id="write-1")
    read = call(arguments=ARGUMENTS)
    success = ToolOutcome({"text": BODY}, False, ("syn-one", "syn-two"))
    outcomes = []
    responses = [reply(calls=[read]), reply("합성 답변")]
    if path == "read_ok":
        outcomes = [success]
    elif path == "tool_error":
        outcomes = [
            ToolOutcome({"error": {"code": "invalid_argument"}}, True, ("syn-one",))
        ]
    elif path == "allowlist":
        responses[0] = reply(calls=[call("invented-private-name")])
    elif path == "invalid_argument":
        responses[0] = reply(calls=[call(arguments="not-json")])
    elif path == "tool_exception":
        outcomes = [RuntimeError("PRIVATE-EXCEPTION-SENTINEL")]
    elif path == "proposal_mixed":
        responses[0] = reply(calls=[write, read])
    elif path == "write_valid":
        responses = [reply(calls=[write])]
    elif path == "check_blocked":
        responses = [reply(calls=[read]), reply(calls=[write])]
        outcomes = [ToolOutcome({"ticket_id": "SYN-1", "text": BODY}, False, ())]
    elif path == "finalizing":
        responses = [reply(calls=[call(id=f"read-{i}")]) for i in range(MAX_STEPS)] + [
            reply(calls=[call(id="final-1"), call(id="final-2")])
        ]
        outcomes = [success] * MAX_STEPS
    else:
        raise AssertionError("unknown synthetic scenario")
    return (
        FakeChat(*responses),
        FakeTools(*outcomes),
        path in {"proposal_mixed", "write_valid", "check_blocked"},
    )


@pytest.mark.parametrize(
    "path,statuses,counts,covered,final_status,executed",
    [
        ("read_ok", ["ok"], [2], 0, "completed", 1),
        ("tool_error", ["tool_error"], [1], 0, "completed", 1),
        ("allowlist", ["tool_not_available"], [0], 0, "completed", 0),
        ("invalid_argument", ["invalid_argument"], [0], 0, "completed", 0),
        ("tool_exception", ["tool_unavailable"], [0], 0, "completed", 1),
        ("proposal_mixed", ["proposal_invalid"] * 2, [0, 0], 0, "completed", 0),
        ("write_valid", [], [], 1, "awaiting_approval", 0),
        ("check_blocked", ["ok"], [0], 1, "blocked", 1),
        (
            "finalizing",
            ["ok"] * MAX_STEPS + ["not_executed"] * 2,
            [2] * MAX_STEPS + [0, 0],
            0,
            "failed",
            MAX_STEPS,
        ),
    ],
)
@async_test
async def test_registered_paths_are_complete(
    audit, auditor, path, statuses, counts, covered, final_status, executed
):
    chat, tools, writes = scenario(path)
    graph, state = await run(chat, tools, audit, auditor, writes=writes)
    rows = audit.records
    events = [r for r in rows if r["event"] == "tool.call"]
    emitted = [
        c
        for message in state["messages"]
        if message["role"] == "assistant"
        for c in message.get("tool_calls", [])
    ]
    drafts = {
        (r["tool"], r["draft_sha256"])
        for r in rows
        if r["event"] == "side_effect.draft"
    }
    assert len(drafts) == covered
    assert len(emitted) == len(events) + covered
    assert [r["status"] for r in events] == statuses
    assert [r["result_count"] for r in events] == counts
    assert all(r["task_id"] == TASK and r["actor_sub"] == USER for r in events)
    assert state["status"] == final_status and len(tools.calls) == executed
    if path == "allowlist":
        assert events[0]["tool_name"] == "unknown"
    if path == "check_blocked":
        assert state["check"]["reason"] == "scope_wider_than_evidence"
        assert any(r.get("check_result") == "blocked" for r in rows)
    if path == "finalizing":
        assert state["error"] == "max_steps"
    assert not hasattr(graph, "agent_tool_audit")


@pytest.mark.parametrize("arguments", ["[1,2]", "null", '{"user_id":"forged"}', None])
@async_test
async def test_pre_execution_argument_rejections(audit, auditor, arguments):
    tools = FakeTools()
    _, state = await run(
        FakeChat(reply(calls=[call(arguments=arguments)]), reply("답")),
        tools,
        None,
        auditor,
    )
    assert [r["status"] for r in audit.records] == ["invalid_argument"]
    assert tools.calls == [] and state["status"] == "completed"


@async_test
async def test_read_only_write_is_not_available(audit, auditor):
    tools = FakeTools()
    await run(
        FakeChat(reply(calls=[call("slack_send_message", WRITE_ARGS)]), reply("답")),
        tools,
        None,
        auditor,
    )
    assert [r["status"] for r in audit.records] == ["tool_not_available"]
    assert tools.calls == []


@pytest.mark.parametrize("error_type", [RuntimeError, ToolNotExecutable])
@async_test
async def test_execution_exception_is_unavailable(audit, auditor, error_type):
    tools = FakeTools(error_type("PRIVATE-EXCEPTION-SENTINEL"))
    _, state = await run(
        FakeChat(reply(calls=[call()]), reply("답")), tools, None, auditor
    )
    assert [r["status"] for r in audit.records] == ["tool_unavailable"]
    assert len(tools.calls) == 1 and state["status"] == "completed"


@pytest.mark.parametrize("existing_draft", [False, True])
@async_test
async def test_other_invalid_proposals(audit, auditor, existing_draft):
    tools = FakeTools()
    write = call("slack_send_message", WRITE_ARGS if existing_draft else "{}")
    graph = assemble(
        FakeChat(reply(calls=[write]), reply("합성 답변")),
        tools,
        audit,
        auditor,
        writes=True,
    )
    initial = build_turn_state(
        task_id=TASK,
        user_id=USER,
        request="합성 요청",
        system_prompt=WITH_WRITES.system_prompt,
        created_at=NOW.isoformat(),
    )
    if existing_draft:
        draft = Draft("slack_send_message", json.loads(WRITE_ARGS))
        initial.update(
            draft={"tool": draft.tool, "args": dict(draft.args)},
            draft_sha256=draft_sha256(draft),
        )
    state = await graph.ainvoke(
        initial,
        {"configurable": {"thread_id": TASK}},
        context=AgentContext(USER),
    )
    events = [r for r in audit.records if r["event"] == "tool.call"]
    assert len(events) == 1 and events[0]["status"] == "proposal_invalid"
    assert events[0]["result_count"] == 0 and events[0]["actor_sub"] == USER
    assert state["status"] == ("awaiting_approval" if existing_draft else "completed")
    assert tools.calls == []


@async_test
async def test_three_calls_have_individual_id_hashes(audit, auditor):
    calls = [call(arguments=ARGUMENTS, id=f"unique-{i}") for i in range(3)]
    tools = FakeTools(*[ToolOutcome({"text": BODY}, False, ()) for _ in calls])
    await run(FakeChat(reply(calls=calls), reply("답")), tools, None, auditor)
    events = audit.records
    assert len(events) == 3
    assert [r["tool_call_sha256"] for r in events] == [
        hashlib.sha256(c["id"].encode()).hexdigest() for c in calls
    ]


@async_test
async def test_records_and_signed_files_contain_no_original_content(
    monkeypatch, tmp_path
):
    attrs = []
    monkeypatch.setattr(
        "govops.agent.graph.set_attributes", lambda **kw: attrs.append(kw)
    )
    key = secrets.token_hex(32)
    monkeypatch.setenv("AUDIT_ARGS_HMAC_KEY", key)
    mac = load_args_mac()
    for sink in (MemoryAuditLog(), AuditLog(tmp_path / "signed")):
        auditor = ToolCallAuditor(sink, mac)
        graph, state = await run(
            FakeChat(reply(calls=[call(arguments=ARGUMENTS)]), reply("답")),
            FakeTools(ToolOutcome({"text": BODY}, False, ())),
            sink,
            auditor,
        )
        if isinstance(sink, MemoryAuditLog):
            serialized = json.dumps(sink.records)
        else:
            files = list(sink.directory.glob("chain-*.jsonl"))
            assert files
            serialized = "".join(path.read_text() for path in files)
        assert '"tool.call"' in serialized
        assert all(s not in serialized for s in (ARGUMENTS, "SYN-987654", key, BODY))
        snapshot = await graph.aget_state({"configurable": {"thread_id": TASK}})
        assert key not in json.dumps(state) + json.dumps(snapshot.values)
        assert key not in json.dumps(attrs)
        assert not hasattr(graph, "agent_tool_audit")


@pytest.mark.parametrize("path", ["read_ok", "proposal_mixed", "finalizing"])
@pytest.mark.parametrize("error_type", [AuditWriteError, RuntimeError])
@async_test
async def test_record_failure_is_content_free_and_fail_open(
    monkeypatch, path, error_type
):
    attrs = []
    monkeypatch.setattr(
        "govops.agent.graph.set_attributes", lambda **kw: attrs.append(kw)
    )
    attempts = []

    class Broken:
        def record(self, **kwargs):
            attempts.append(kwargs["tool_call_id"])
            raise error_type("PRIVATE-EXCEPTION-SENTINEL")

    chat, tools, writes = scenario(path)
    _, actual = await run(chat, tools, MemoryAuditLog(), Broken(), writes=writes)
    baseline_chat, baseline_tools, writes = scenario(path)
    _, baseline = await run(
        baseline_chat, baseline_tools, MemoryAuditLog(), None, writes=writes
    )
    assert actual == baseline
    assert tools.calls == baseline_tools.calls and chat.calls == baseline_chat.calls
    assert attempts and len(attempts) == sum(
        len(m.get("tool_calls", []))
        for m in actual["messages"]
        if m["role"] == "assistant"
    )
    if path != "proposal_mixed":
        assert {"tool.audit_gap": True} in attrs
    assert "PRIVATE-EXCEPTION-SENTINEL" not in json.dumps(attrs)


@pytest.mark.parametrize(
    "path", ["read_ok", "proposal_mixed", "write_valid", "check_blocked", "finalizing"]
)
@async_test
async def test_none_changes_only_tool_call_records(mac, path):
    sinks = [MemoryAuditLog(), MemoryAuditLog()]
    states = []
    for sink, recorder in zip(
        sinks, (ToolCallAuditor(sinks[0], mac), None), strict=True
    ):
        chat, tools, writes = scenario(path)
        graph, _ = await run(chat, tools, sink, recorder, writes=writes)
        # Compare persisted state; interrupt delivery IDs are fresh per graph run.
        snapshot = await graph.aget_state({"configurable": {"thread_id": TASK}})
        states.append(snapshot.values)
    assert states[0] == states[1]
    assert not any(r["event"] == "tool.call" for r in sinks[1].records)
    assert [r for r in sinks[0].records if r["event"] != "tool.call"] == sinks[
        1
    ].records


@pytest.mark.parametrize("finalizing", [False, True])
@async_test
async def test_record_does_not_block_event_loop(auditor, finalizing):
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    completed = []

    class Blocking:
        def record(self, **kwargs):
            if (kwargs["status"] == "not_executed") == finalizing:
                loop.call_soon_threadsafe(entered.set)
                completed.append(release.wait(timeout=5))
            auditor.record(**kwargs)

    async def unblock():
        await asyncio.wait_for(entered.wait(), timeout=6)
        release.set()

    chat, tools, writes = scenario("finalizing" if finalizing else "read_ok")
    (_, state), _ = await asyncio.gather(
        run(chat, tools, None, Blocking(), writes=writes), unblock()
    )
    assert completed and all(completed)
    assert state["status"] == ("failed" if finalizing else "completed")


def test_audit_tool_names_match_all_profiles():
    names = {
        tool["function"]["name"]
        for profile in PROFILES.values()
        for tool in profile.tools
    }
    assert names
    assert TOOL_CALL_NAMES - {"unknown"} == names
