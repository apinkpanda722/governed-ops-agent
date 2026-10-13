"""Retention contracts use real graph checkpoints and injected future time."""

import asyncio
from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from test_agent_graph import FakeChat, FakeTools, async_test, reply

from govops.agent.graph import AgentState, build_graph, get_task, run_task
from govops.agent.retention import RETENTION_DAYS, prune_checkpoints

ANSWER = "합성 답변 원문 sentinel"
MESSAGE = "합성 메시지 원문 sentinel"
DRAFT = "합성 초안 원문 sentinel"


async def saved_thread(saver, thread_id, status, expires_at=None):
    # Execute graph nodes rather than fabricating saver tuples or changing ts.
    builder = StateGraph(AgentState)

    def state_node(state):
        values = {
            "answer": ANSWER,
            "messages": [{"role": "user", "content": MESSAGE}],
            "draft": {"text": DRAFT},
        }
        if status is not None:
            values["status"] = status
        if expires_at is not None:
            values["expires_at"] = expires_at
        return values

    builder.add_node("state", state_node)
    builder.add_edge(START, "state")
    if status == "awaiting_approval":
        builder.add_node("approval", lambda state: interrupt({"draft": state["draft"]}))
        builder.add_edge("state", "approval")
        builder.add_edge("approval", END)
    else:
        builder.add_edge("state", END)
    graph = builder.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": thread_id}}
    await graph.ainvoke({"request": MESSAGE}, config)
    checkpoint = await saver.aget_tuple(config)
    return graph, datetime.fromisoformat(checkpoint.checkpoint["ts"])


@pytest.mark.parametrize(
    "status,days,expiry,target,active",
    [
        *[
            (status, days, None, days == 8, False)
            for status in (
                "completed",
                "failed",
                "rejected",
                "expired",
                "blocked_input",
            )
            for days in (6, 8)
        ],
        ("running", 8, None, False, True),
        ("awaiting_approval", 8, "future", False, True),
        ("awaiting_approval", 8, "past", True, False),
        ("awaiting_approval", 8, None, False, True),
        ("awaiting_approval", 8, "invalid", False, True),
        ("awaiting_approval", 8, "naive", False, True),
        (None, 8, None, False, True),
    ],
)
@async_test
async def test_status_age_matrix(status, days, expiry, target, active):
    saver = InMemorySaver()
    base = datetime.now(UTC)
    expires_at = {
        "future": (base + timedelta(days=10)).isoformat(),
        "past": (base + timedelta(days=1)).isoformat(),
        "invalid": "not-a-date",
        "naive": "2026-10-01T00:00:00",
    }.get(expiry)
    graph, ts = await saved_thread(saver, "synthetic-thread", status, expires_at)
    saver.adelete_thread = AsyncMock(wraps=saver.adelete_thread)
    now = ts + timedelta(days=days)
    result = await prune_checkpoints(saver, now=now)
    assert result.cutoff == (now - timedelta(days=7)).isoformat()
    assert result.scanned == 1
    assert result.active == int(active)
    assert result.candidates == (("synthetic-thread",) if target else ())
    assert result.deleted == 0
    assert result.deleted_ids == ()
    saver.adelete_thread.assert_not_called()
    assert await get_task(graph, "synthetic-thread")
    for rendered in (
        repr(result),
        str(asdict(result)),
        result.cutoff,
        *result.candidates,
    ):
        assert all(raw not in rendered for raw in (ANSWER, MESSAGE, DRAFT))
    with pytest.raises(FrozenInstanceError):
        result.deleted = 1


@pytest.mark.parametrize("marked", [False, True])
@pytest.mark.parametrize(
    "status,expiry,active",
    [
        ("completed", None, False),
        ("running", None, True),
        ("awaiting_approval", "future", True),
        ("awaiting_approval", "past", False),
        ("awaiting_approval", None, True),
        ("awaiting_approval", "invalid", True),
        (None, None, True),
    ],
)
@async_test
async def test_deleted_recent_thread_preserves_active_priority(
    marked, status, expiry, active
):
    saver = InMemorySaver()
    base = datetime.now(UTC)
    expires_at = {
        "future": (base + timedelta(hours=2)).isoformat(),
        "past": base.isoformat(),
        "invalid": "not-a-date",
    }.get(expiry)
    graph, ts = await saved_thread(saver, "recent", status, expires_at)
    saver.adelete_thread = AsyncMock(wraps=saver.adelete_thread)
    result = await prune_checkpoints(
        saver,
        now=ts + timedelta(hours=1),
        apply=True,
        deleted_conversation_ids=["recent"] if marked else [],
    )
    target = marked and not active
    assert result.scanned == 1
    assert result.active == int(active)
    assert result.candidates == result.deleted_ids == (("recent",) if target else ())
    assert result.deleted == int(target)
    assert saver.adelete_thread.await_count == int(target)
    assert (await get_task(graph, "recent") is None) == target


@pytest.mark.parametrize("timestamp", [None, "invalid", "2026-10-01T00:00:00"])
@async_test
async def test_deleted_thread_with_unknown_timestamp_stays_active(timestamp):
    saver = InMemorySaver()
    await saved_thread(saver, "unknown", "completed")
    checkpoints = [item async for item in saver.alist(None)]
    latest = max(datetime.fromisoformat(item.checkpoint["ts"]) for item in checkpoints)
    # Even an older checkpoint of unknown age prevents proving the latest state.
    checkpoints[-1].checkpoint["ts"] = timestamp

    async def listed(config):
        for item in checkpoints:
            yield item

    saver.alist = listed
    saver.adelete_thread = AsyncMock(wraps=saver.adelete_thread)
    result = await prune_checkpoints(
        saver,
        now=latest + timedelta(days=8),
        apply=True,
        deleted_conversation_ids={"unknown"},
    )
    assert result.scanned == result.active == 1
    assert result.candidates == result.deleted_ids == ()
    assert result.deleted == 0
    saver.adelete_thread.assert_not_called()


@pytest.mark.parametrize("extra_seconds,target", [(0, False), (1, True)])
@async_test
async def test_strict_boundary(extra_seconds, target):
    saver = InMemorySaver()
    _, ts = await saved_thread(saver, "boundary", "completed")
    result = await prune_checkpoints(
        saver, now=ts + timedelta(days=RETENTION_DAYS, seconds=extra_seconds)
    )
    assert result.candidates == (("boundary",) if target else ())


@async_test
async def test_apply_deletes_whole_thread_and_preserves_active():
    saver = InMemorySaver()
    graph = build_graph(FakeChat(reply(ANSWER)), FakeTools(), saver)
    await run_task(graph, task_id="done", user_id="synthetic-user", request=MESSAGE)
    checkpoint = await saver.aget_tuple({"configurable": {"thread_id": "done"}})
    _, ts = await saved_thread(saver, "active", "running")
    approval_graph, _ = await saved_thread(
        saver, "waiting", "awaiting_approval", (ts + timedelta(days=10)).isoformat()
    )
    result = await prune_checkpoints(saver, now=ts + timedelta(days=8), apply=True)
    assert result.scanned == 3
    assert result.active == 2
    assert result.candidates == result.deleted_ids == ("done",)
    assert result.deleted == len(result.deleted_ids) == 1
    assert checkpoint.checkpoint["channel_values"]["answer"] == ANSWER
    assert await get_task(graph, "done") is None
    assert (await get_task(graph, "active"))["status"] == "running"
    assert (await get_task(approval_graph, "waiting"))["status"] == "awaiting_approval"
    assert (
        (await approval_graph.aget_state({"configurable": {"thread_id": "waiting"}}))
        .tasks[0]
        .interrupts
    )
    assert not [c async for c in saver.alist({"configurable": {"thread_id": "done"}})]


@async_test
async def test_failure_continues_without_disclosing_content(caplog, capsys):
    saver = InMemorySaver()
    graphs = {}
    for thread_id in ("a-fails", "b-deleted", "c-deleted"):
        graphs[thread_id], ts = await saved_thread(saver, thread_id, "completed")
    delete = saver.adelete_thread

    async def failing(thread_id):
        if thread_id == "a-fails":
            raise RuntimeError(f"{MESSAGE} {ANSWER} {DRAFT} synthetic-user")
        await delete(thread_id)

    saver.adelete_thread = AsyncMock(side_effect=failing)
    result = await prune_checkpoints(saver, now=ts + timedelta(days=8), apply=True)
    assert result.candidates == ("a-fails", "b-deleted", "c-deleted")
    assert result.deleted_ids == ("b-deleted", "c-deleted")
    assert result.deleted == len(result.deleted_ids) == 2
    assert saver.adelete_thread.await_count == 3
    assert await get_task(graphs["a-fails"], "a-fails")
    for thread_id in result.deleted_ids:
        assert await get_task(graphs[thread_id], thread_id) is None
    assert caplog.text == ""
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    assert all(
        raw not in repr(result) for raw in (MESSAGE, ANSWER, DRAFT, "synthetic-user")
    )


@async_test
async def test_latest_timestamp_not_iteration_order():
    saver = InMemorySaver()
    graph, _ = await saved_thread(saver, "changed", "running")
    await graph.aupdate_state(
        {"configurable": {"thread_id": "changed"}}, {"status": "completed"}
    )
    checkpoints = [c async for c in saver.alist(None)]
    latest = max(datetime.fromisoformat(c.checkpoint["ts"]) for c in checkpoints)

    async def oldest_first(config):
        for c in sorted(checkpoints, key=lambda c: c.checkpoint["ts"]):
            yield c

    saver.alist = oldest_first
    result = await prune_checkpoints(saver, now=latest + timedelta(days=8))
    assert result.scanned == 1
    assert result.active == 0
    assert result.candidates == ("changed",)


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 10, 8),  # noqa: DTZ001 — deliberately reject naive input
        datetime(2026, 10, 8, tzinfo=timezone(timedelta(hours=9))),
    ],
)
def test_now_must_be_aware_utc(now):
    saver = InMemorySaver()
    saver.alist = AsyncMock()
    with pytest.raises(ValueError, match="UTC"):
        asyncio.run(prune_checkpoints(saver, now=now))
    saver.alist.assert_not_called()


@async_test
async def test_empty_and_custom_retention():
    saver = InMemorySaver()
    result = await prune_checkpoints(saver, now=datetime.now(UTC), apply=True)
    assert (result.scanned, result.active, result.deleted) == (0, 0, 0)
    assert result.candidates == result.deleted_ids == ()
    _, ts = await saved_thread(saver, "custom", "completed")
    result = await prune_checkpoints(
        saver, now=ts + timedelta(days=6), retention_days=5
    )
    assert result.candidates == ("custom",)
    assert result.cutoff == (ts + timedelta(days=1)).isoformat()


@pytest.mark.parametrize("expired", [False, True])
@async_test
async def test_real_write_profile_approval(expired):
    from types import SimpleNamespace

    from test_agent_graph import call

    from govops.agent.graph import WITH_WRITES, EffectDeps
    from govops.agent.scope import ScopeIndex
    from govops.agent.users import UserInfo
    from govops.guardrails.policy import TeamPolicy
    from govops.tools.outbox import InMemoryOutboxStore
    from govops.tools.resilience import ToolPolicy

    saver = InMemorySaver()
    user = UserInfo("demo-platform-g3", "platform", 3)
    tools = FakeTools()
    tools.policy = ToolPolicy()
    # Only the approval clock is injected; saver still writes its own actual ts.
    approval_clock = datetime.now(UTC) + timedelta(days=0 if expired else 10)
    graph = build_graph(
        FakeChat(
            reply(
                calls=[
                    call(
                        "slack_send_message",
                        '{"channel":"team-platform","text":"합성 안내"}',
                    )
                ]
            )
        ),
        tools,
        saver,
        profile=WITH_WRITES,
        effects=EffectDeps(
            jira=SimpleNamespace(),
            slack=SimpleNamespace(),
            outbox=InMemoryOutboxStore(),
        ),
        audit=SimpleNamespace(append=lambda *args, **kwargs: None),
        scope=ScopeIndex({}, {}, (user,)),
        policy=TeamPolicy({"platform": "block"}),
        users={user.label: user},
        clock=lambda: approval_clock,
    )
    state = await run_task(
        graph, task_id="write-waiting", user_id=user.label, request="합성 안내 발송"
    )
    assert state["status"] == "awaiting_approval"
    checkpoint = await saver.aget_tuple(
        {"configurable": {"thread_id": "write-waiting"}}
    )
    ts = datetime.fromisoformat(checkpoint.checkpoint["ts"])
    before = await get_task(graph, "write-waiting")
    config = {"configurable": {"thread_id": "write-waiting"}}
    interrupts_before = (await graph.aget_state(config)).tasks
    assert interrupts_before[0].interrupts
    result = await prune_checkpoints(saver, now=ts + timedelta(days=8), apply=True)
    assert result.deleted_ids == (("write-waiting",) if expired else ())
    remaining = await get_task(graph, "write-waiting")
    if expired:
        assert remaining is None
    else:
        assert remaining == before
        assert (await graph.aget_state(config)).tasks == interrupts_before
