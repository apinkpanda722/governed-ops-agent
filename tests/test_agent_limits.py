"""Registered turn limits using synthetic calls and preserved official evidence."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from test_agent_graph import FakeChat, FakeTools, async_test, call, reply
from test_obs_trace import Sink

from govops.agent.exploration import (
    EXPLORATION_ANSWER,
    EXPLORATION_N,
    STOP_REASON,
    ExplorationCounter,
    exploration_budget_from_env,
    identifiers,
    longest_streak,
)
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
from govops.audit.log import AuditLog
from govops.evalrun import explore
from govops.evalrun.regrade import n_candidate
from govops.evalrun.tool_trace import extract_tool_trace, structure
from govops.guardrails.policy import TeamPolicy
from govops.obs.trace import SpanKind, trace
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.registry import ToolNotExecutable, ToolOutcome
from govops.tools.resilience import ToolPolicy

ROOT = Path(__file__).resolve().parents[1]
RUNS = ("26c8553905bb4396b20ef2b2ff6399f1", "e36589778c2243388f09d9b4d3b60f2b")


def test_registered_counter_reproduction():
    registration = (ROOT / "docs/results/n-registration-m5.md").read_text()
    assert f"**N = {EXPLORATION_N}**" in registration
    assert explore.identifiers is identifiers
    assert explore.longest_streak is longest_streak
    maxima = []
    for run, expected in zip(RUNS, (3, 4), strict=True):
        directory = ROOT / "data/results/dev" / run
        path = directory / "observations.jsonl"
        assert hashlib.sha256(path.read_bytes()).hexdigest() in registration
        result = n_candidate(directory, rules="m3-grading")
        assert result["candidate"] == expected
        measured = {}
        for row in map(json.loads, path.read_text().splitlines()):
            counter = ExplorationCounter()
            maximum = 0
            for entry in extract_tool_trace(row["tool_results"])[1]:
                maximum = max(maximum, counter.observe(entry["result"]))
            measured[row["task_id"]] = maximum
        assert {task: measured[task] for task in result["inputs"]} == result["inputs"]
        assert measured["T2-02"] == expected - 1
        maxima.extend(result["inputs"].values())
    assert max(maxima) + 1 == EXPLORATION_N == 4


@pytest.mark.parametrize(
    "payload",
    [
        {
            "text": "syn-text",
            "count": 3,
            "nested": [{"doc_id": "syn-1", "key": "SYN-1"}],
        },
        {
            "ticket_id": "SYN-2",
            "doc_id": 12,
            "other": ["syn-ignored", {"key": "SYN-3"}],
        },
        {"columns": ["count"], "rows": [[42]], "text": "syn-1"},
    ],
)
def test_structure_identifiers(payload):
    assert identifiers(payload) == identifiers(structure(payload))
    if "columns" in payload:
        assert identifiers(payload) == set()
        assert ExplorationCounter().observe(payload) == 1


def test_counter_state_and_reset():
    counter = ExplorationCounter()
    assert counter.observe({}) == 1
    assert counter.observe({"doc_id": "syn-b"}) == 0
    assert counter.observe({"key": "syn-b"}) == 1
    assert counter.observe({"doc_id": "syn-a"}) == 0
    state = counter.to_state()
    assert state == {"explored_ids": ["syn-a", "syn-b"], "no_new_info_streak": 0}
    assert ExplorationCounter.from_state(state).to_state() == state
    previous = {
        **state,
        "task_id": "old",
        "user_id": "demo-platform-g3",
        "created_at": "fixed",
        "no_new_info_streak": 4,
        "turn_cost_usd": 0.4,
    }
    new = build_turn_state(
        task_id="new",
        user_id="demo-platform-g3",
        request="합성 요청",
        system_prompt="system",
        created_at="fixed",
        previous=previous,
    )
    assert ExplorationCounter.from_state(new).to_state() == {
        "explored_ids": [],
        "no_new_info_streak": 0,
    }
    assert new["turn_cost_usd"] == 0
    assert ExplorationCounter.from_state(previous).exhausted(4)


@pytest.mark.parametrize("value,expected", [("", 4), (" ", 4), ("0", None), ("1", 4)])
def test_environment_flag(monkeypatch, value, expected):
    monkeypatch.setenv("GOVOPS_EXPLORATION_BUDGET", value)
    assert exploration_budget_from_env() == expected


@pytest.mark.parametrize("value", ["4", "true", "-1"])
def test_environment_flag_invalid(monkeypatch, value):
    monkeypatch.setenv("GOVOPS_EXPLORATION_BUDGET", value)
    with pytest.raises(ValueError):
        exploration_budget_from_env()


def test_environment_default():
    assert exploration_budget_from_env() == EXPLORATION_N


async def execute(chat, tools, **kwargs):
    graph = build_graph(chat, tools, InMemorySaver(), **kwargs)
    return await run_task(
        graph,
        task_id="00000000000000000000000000000000",
        user_id="demo-platform-g3",
        request="합성 조회",
    )


@async_test
async def test_exhaustion_and_per_call_count():
    for batch in (1, 4):
        chat = FakeChat(
            *(
                reply(calls=[call(id=f"call-{i}") for i in range(batch)])
                for _ in range(4 // batch)
            )
        )
        tools = FakeTools(*(ToolOutcome({}, False, ()) for _ in range(4)))
        sink = Sink()
        with trace("0" * 32, sinks=[sink]):
            state = await execute(chat, tools)
        nodes = [r for r in sink.records if r.kind == SpanKind.GRAPH_NODE]
        assert nodes[-1].attributes["stop_reason"] == STOP_REASON
        assert state["status"] == "completed"
        assert state["answer"] == EXPLORATION_ANSWER
        assert state["cited_doc_ids"] == []
        assert state["stop_reason"] == STOP_REASON
        assert state["steps"] == len(chat.calls) == 4 // batch < MAX_STEPS
        assert state["no_new_info_streak"] == len(tools.calls) == 4


@async_test
async def test_new_id_resets_and_batch_finishes_before_check():
    chat = FakeChat(
        reply(calls=[call(id=str(i)) for i in range(5)]), reply("계속 답변")
    )
    tools = FakeTools(
        *(ToolOutcome({}, False, ()) for _ in range(4)),
        ToolOutcome({"doc_id": "syn-new"}, False, ()),
    )
    state = await execute(chat, tools)
    assert state["answer"] == "계속 답변"
    assert state["no_new_info_streak"] == 0
    assert state["explored_ids"] == ["syn-new"]
    assert len(chat.calls) == 2
    # Three empty calls followed by a new ID also reset before the next model.
    chat = FakeChat(*(reply(calls=[call()]) for _ in range(4)), reply("계속"))
    state = await execute(
        chat,
        FakeTools(
            *(ToolOutcome({}, False, ()) for _ in range(3)),
            ToolOutcome({"key": "SYN-new"}, False, ()),
        ),
    )
    assert state["no_new_info_streak"] == 0
    assert len(chat.calls) == 5


@async_test
async def test_only_reached_calls_count_including_errors():
    calls = [
        call(arguments="[]"),
        call(name="unknown"),
        *(call(id=str(i)) for i in range(4)),
    ]
    chat = FakeChat(reply(calls=calls))
    tools = FakeTools(
        RuntimeError("synthetic"),
        ToolNotExecutable(),
        ToolOutcome({}, True, ()),
        ToolOutcome({}, False, ()),
    )
    state = await execute(chat, tools)
    assert len(tools.calls) == state["no_new_info_streak"] == 4
    assert state["stop_reason"] == STOP_REASON


@async_test
async def test_disabled_and_explicit_budget():
    chat = FakeChat(*(reply(calls=[call()]) for _ in range(MAX_STEPS)))
    state = await execute(
        chat,
        FakeTools(*(ToolOutcome({}, True, ()) for _ in range(MAX_STEPS))),
        exploration_budget=None,
    )
    assert state["status"] == "failed"
    assert state["error"] == "max_steps"
    assert state["steps"] == len(chat.calls) == MAX_STEPS
    chat = FakeChat(reply(calls=[call()]))
    state = await execute(
        chat, FakeTools(ToolOutcome({}, False, ())), exploration_budget=1
    )
    assert state["stop_reason"] == STOP_REASON


@async_test
async def test_cost_exact_limit_before_next_call():
    chat = FakeChat(
        *(replace(reply(calls=[call()]), cost_usd=MAX_COST_USD / 2) for _ in range(2)),
        reply("호출 금지"),
    )
    state = await execute(
        chat,
        FakeTools(*(ToolOutcome({"doc_id": f"syn-{i}"}, False, ()) for i in range(2))),
    )
    assert state["status"] == "failed"
    assert state["error"] == "max_cost"
    assert state["turn_cost_usd"] == MAX_COST_USD
    assert len(chat.calls) == state["steps"] == 2


@async_test
@pytest.mark.parametrize("cost", [0.01, 0.41])
async def test_cost_last_response_used(cost):
    chat = FakeChat(
        replace(reply(calls=[call()]), cost_usd=0.1),
        replace(reply("답변"), cost_usd=cost),
    )
    state = await execute(chat, FakeTools(ToolOutcome({}, False, ())))
    assert state["status"] == "completed"
    assert state["answer"] == "답변"
    assert state["turn_cost_usd"] == pytest.approx(0.1 + cost)
    assert len(chat.calls) == 2


def write_graph(chat, tools, tmp_path):
    user = UserInfo("demo-platform-g3", "platform", 3)
    tools.policy = ToolPolicy()

    class Slack:
        async def send_message(self, user_id, **kwargs):
            return {"message": {"message_id": "synthetic-message"}}

    class Jira:
        async def add_comment(self, *args, **kwargs):
            pytest.fail("Unexpected Jira effect")

        async def create_followup(self, *args, **kwargs):
            pytest.fail("Unexpected Jira effect")

    return build_graph(
        chat,
        tools,
        InMemorySaver(),
        profile=WITH_WRITES,
        effects=EffectDeps(Jira(), Slack(), InMemoryOutboxStore()),
        audit=AuditLog(tmp_path),
        scope=ScopeIndex({}, {}, (user,)),
        policy=TeamPolicy({"platform": "block"}),
        users={user.label: user},
    )


@async_test
async def test_write_exhaustion_skips_finish(tmp_path):
    chat = FakeChat(reply(calls=[call(id=str(i)) for i in range(4)]))
    graph = write_graph(
        chat, FakeTools(*(ToolOutcome({}, False, ()) for _ in range(4))), tmp_path
    )
    events = [
        event
        async for event in graph.astream(
            build_turn_state(
                task_id="00000000000000000000000000000000",
                user_id="demo-platform-g3",
                request="조회",
                system_prompt="system",
                created_at="fixed",
            ),
            {"configurable": {"thread_id": "00000000000000000000000000000000"}},
            context=AgentContext("demo-platform-g3"),
            stream_mode="updates",
        )
    ]
    assert [next(iter(event)) for event in events] == ["model", "read_tools"]
    assert events[-1]["read_tools"]["answer"] == EXPLORATION_ANSWER


@async_test
async def test_cost_preserved_through_approval_resume(tmp_path):
    chat = FakeChat(
        replace(reply(calls=[call()]), cost_usd=0.125),
        replace(
            reply(
                calls=[
                    call(
                        "slack_send_message",
                        '{"channel":"team-platform","text":"합성 안내"}',
                    )
                ]
            ),
            cost_usd=0.125,
        ),
    )
    graph = write_graph(chat, FakeTools(ToolOutcome({}, False, ())), tmp_path)
    state = await run_task(
        graph,
        task_id="00000000000000000000000000000000",
        user_id="demo-platform-g3",
        request="안내",
    )
    assert state["status"] == "awaiting_approval"
    assert state["turn_cost_usd"] == 0.25
    # Invalid resume keeps the approval pending and makes no model call.
    resumed = await graph.ainvoke(
        Command(resume={"decision": "hold"}),
        {"configurable": {"thread_id": "00000000000000000000000000000000"}},
    )
    assert resumed["status"] == "awaiting_approval"
    assert resumed["turn_cost_usd"] == 0.25
    assert len(chat.calls) == 2
    approved = await graph.ainvoke(
        Command(
            resume={
                "decision": "approve",
                "approver": "demo-platform-g3",
                "draft_sha256": state["draft_sha256"],
            }
        ),
        {"configurable": {"thread_id": "00000000000000000000000000000000"}},
    )
    assert approved["status"] == "completed"
    assert approved["turn_cost_usd"] == 0.25
    assert len(chat.calls) == 2


@async_test
@pytest.mark.parametrize("flag", ["0", "1", ""])
async def test_graph_uses_environment_default(monkeypatch, flag):
    monkeypatch.setenv("GOVOPS_EXPLORATION_BUDGET", flag)
    chat = FakeChat(*(reply(calls=[call()]) for _ in range(4)), reply("계속"))
    state = await execute(
        chat, FakeTools(*(ToolOutcome({}, False, ()) for _ in range(4)))
    )
    assert state["answer"] == ("계속" if flag == "0" else EXPLORATION_ANSWER)
    assert len(chat.calls) == (5 if flag == "0" else 4)
