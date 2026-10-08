import asyncio
from types import SimpleNamespace

from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, FakeTools, async_test, call, reply

from govops.agent.graph import build_graph
from govops.agent.scope import ScopeIndex
from govops.agent.turns import MAX_TURNS
from govops.api.tasks import TaskService
from govops.guardrails.policy import TeamPolicy


@async_test
async def test_conversation_turns_and_limit():
    service = TaskService(
        build_graph(
            FakeChat(*(reply("합성 답변") for _ in range(MAX_TURNS))),
            FakeTools(),
            InMemorySaver(),
        )
    )
    first = await service.start_conversation("a", "조회")
    conversation = first["conversation_id"]
    assert first["turn_id"] == conversation
    ids = {conversation}
    for _ in range(MAX_TURNS - 1):
        await asyncio.wait_for(asyncio.gather(*service._tasks), 10)
        result = await service.add_turn("a", conversation, "다음 조회")
        ids.add(result["turn_id"])
        assert result["conversation_id"] == conversation
    await asyncio.wait_for(asyncio.gather(*service._tasks), 10)
    assert len(ids) == MAX_TURNS
    assert await service.get("a", conversation) is None
    import pytest

    from govops.api.conversations import ConversationError

    with pytest.raises(ConversationError) as error:
        await service.add_turn("a", conversation, "초과")
    assert (error.value.status_code, error.value.code) == (409, "turn_limit")
    await service.aclose()


@async_test
async def test_routes_running_concurrency_and_ownership():
    from uuid import uuid4

    import httpx

    from govops.api.app import create_app
    from govops.api.sessions import SessionStore

    gate = asyncio.Event()

    class HeldChat:
        async def complete(self, **kwargs):
            await asyncio.wait_for(gate.wait(), 10)
            return reply("합성 답변")

    service = TaskService(build_graph(HeldChat(), FakeTools(), InMemorySaver()))
    sessions = SessionStore()
    app = create_app(
        users=frozenset({"a", "b"}),
        sessions=sessions,
        demo_login=False,
        service=service,
    )
    tokens = {u: sessions.issue(u)[0] for u in ("a", "b")}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:

        async def post(path, user="a", value="조회"):
            return await asyncio.wait_for(
                client.post(
                    path,
                    json={"request": value},
                    headers={"Authorization": "Bearer " + tokens[user]},
                ),
                10,
            )

        first = await post("/conversations")
        assert first.status_code == 202
        ids = first.json()
        assert set(ids) == {"conversation_id", "turn_id"}
        assert ids["conversation_id"] == ids["turn_id"]
        path = "/conversations/" + ids["conversation_id"] + "/turns"
        denied = await post(path, "b")
        absent = await post("/conversations/" + uuid4().hex + "/turns", "b")
        assert (denied.status_code, denied.content, dict(denied.headers)) == (
            absent.status_code,
            absent.content,
            dict(absent.headers),
        )
        assert denied.status_code == 404
        running = await post(path)
        assert (running.status_code, running.json()) == (
            409,
            {"detail": "turn_in_progress"},
        )
        gate.set()
        await asyncio.wait_for(asyncio.gather(*service._tasks), 10)
        gate.clear()
        responses = await asyncio.wait_for(asyncio.gather(post(path), post(path)), 10)
        assert sorted(r.status_code for r in responses) == [202, 409]
        assert {r.json()["detail"] for r in responses if r.status_code == 409} == {
            "turn_in_progress"
        }
        gate.set()
        await asyncio.wait_for(asyncio.gather(*service._tasks), 10)
        turn = next(r.json()["turn_id"] for r in responses if r.status_code == 202)
        assert turn != ids["conversation_id"]
        assert (await service.get("a", turn))["status"] == "completed"
        assert await service.get("a", ids["turn_id"]) is None
        assert (await post(path, value=" ")).json() == {"detail": "invalid_request"}
    await service.aclose()


@async_test
async def test_index_idempotence_and_deletion():
    from govops.api.conversations import InMemoryTurnIndex

    index = InMemoryTurnIndex()
    row = {
        "turn_id": "turn",
        "conversation_id": "conversation",
        "owner_sub": "a",
        "turn_index": 0,
        "created_at": "synthetic-time",
    }
    await index.add(**row)
    await index.set_awaiting("turn", True)
    await index.add(**{**row, "conversation_id": "other"})
    await index.set_awaiting("absent", True)
    assert await index.conversation_of("turn") == "conversation"
    assert await index.conversation_of("absent") is None
    assert set(await index.awaiting()) == {("turn", "conversation")}
    assert await index.delete_conversations(["other"]) == 0
    assert await index.delete_conversations(["conversation"]) == 1
    assert await index.awaiting() == []


def write_service(tmp_path):
    import json
    from datetime import UTC, datetime

    from govops.agent.graph import WITH_WRITES, EffectDeps
    from govops.agent.scope import ScopeIndex
    from govops.agent.users import UserInfo
    from govops.audit.log import AuditLog
    from govops.guardrails.policy import TeamPolicy
    from govops.tools.outbox import InMemoryOutboxStore
    from govops.tools.resilience import ToolPolicy

    users = {
        u.label: u
        for u in (
            UserInfo("demo-requester-g3", "platform", 6),
            UserInfo("demo-approver-g3", "platform", 4),
            UserInfo("demo-junior-g3", "platform", 3),
            UserInfo("demo-other-g3", "finance", 4),
        )
    }
    now = [datetime(2026, 10, 5, tzinfo=UTC)]

    class Slack:
        def __init__(self):
            self.sent = []

        async def send_message(self, user_id, **args):
            self.sent.append((user_id, args))
            return {"message": {"message_id": "message-1"}}

    slack = Slack()
    tools = FakeTools()
    tools.policy = ToolPolicy()
    saver = InMemorySaver()
    effects = EffectDeps(
        SimpleNamespace(add_comment=None, create_followup=None),
        slack,
        InMemoryOutboxStore(),
    )
    audit = AuditLog(tmp_path)
    graph = build_graph(
        FakeChat(
            *[
                reply(
                    calls=[
                        call(
                            "slack_send_message",
                            json.dumps({"channel": "announce", "text": "합성 안내"}),
                        )
                    ]
                )
                for _ in range(8)
            ]
        ),
        tools,
        saver,
        profile=WITH_WRITES,
        effects=effects,
        audit=audit,
        scope=ScopeIndex({}, {}, tuple(users.values())),
        policy=TeamPolicy({"platform": "block"}),
        users=users,
        clock=lambda: now[0],
    )
    service = TaskService(
        graph, users=users, effects=effects, audit=audit, policy=tools.policy
    )
    return service, saver, slack, now, users, tools, effects, audit


@async_test
async def test_pending_restart_late_approval_and_mixed_locks(tmp_path):
    import pytest

    from govops.api.conversations import ConversationError
    from govops.api.tasks import ApprovalError

    service, saver, slack, now, users, tools, effects, audit = write_service(tmp_path)
    owner, approver = "demo-requester-g3", "demo-approver-g3"
    conversation = await service.start(owner, "합성 공지")
    await asyncio.wait_for(asyncio.gather(*service._tasks), 10)
    view = await service.get(owner, conversation)
    body = {"decision": "approve", "draft_sha256": view["draft_sha256"]}
    for user, code in (
        (owner, "approval_pending"),
        (approver, "conversation_not_found"),
    ):
        with pytest.raises(ConversationError) as error:
            await service.add_turn(user, conversation, "다음")
        assert error.value.code == code
    snapshot = await service.graph.aget_state(service._config(conversation))
    assert sum(bool(task.interrupts) for task in snapshot.tasks) == 1
    assert set(await service.turns.awaiting()) == {(conversation, conversation)}
    await asyncio.wait_for(service.approve(approver, conversation, **body), 10)
    await asyncio.wait_for(service.approve(approver, conversation, **body), 10)
    assert len(slack.sent) == 1
    second = await service.add_turn(owner, conversation, "다음 합성 공지")
    await asyncio.wait_for(asyncio.gather(*service._tasks), 10)
    turn = second["turn_id"]
    assert turn != conversation
    fresh_graph = build_graph(
        FakeChat(),
        tools,
        saver,
        profile=service.graph.agent_profile,
        effects=effects,
        audit=audit,
        scope=ScopeIndex({}, {}, tuple(users.values())),
        policy=TeamPolicy({"platform": "block"}),
        users=users,
        clock=lambda: now[0],
    )
    fresh = TaskService(
        fresh_graph,
        turns=service.turns,
        users=users,
        effects=effects,
        audit=audit,
        clock=lambda: now[0],
    )
    await fresh.startup_cleanup()
    view = await fresh.get(owner, turn)
    body2 = {"decision": "approve", "draft_sha256": view["draft_sha256"]}

    async def action(kind):
        try:
            if kind == "approve":
                return await fresh.approve(approver, turn, **body2)
            if kind == "turn":
                return await fresh.add_turn(owner, conversation, "다음")
            return await fresh.get(owner, turn)
        except (ApprovalError, ConversationError) as error:
            return error.status_code

    await asyncio.wait_for(
        asyncio.gather(
            *(
                asyncio.wait_for(action(kind), 10)
                for kind in ["approve", "turn", "get"] * 20
            )
        ),
        10,
    )
    await asyncio.wait_for(asyncio.gather(*fresh._tasks), 10)
    assert len(slack.sent) == 2
    with pytest.raises(ApprovalError) as error:
        await fresh.approve(approver, conversation, **body)
    assert error.value.status_code == 404
    assert len(slack.sent) == 2
    await fresh.aclose()
    await service.aclose()


@async_test
async def test_expiry_closes_interrupt_before_new_turn(tmp_path):
    import json
    from datetime import timedelta

    service, _, slack, now, _, _, _, audit = write_service(tmp_path)
    conversation = await service.start("demo-requester-g3", "합성 공지")
    await asyncio.wait_for(asyncio.gather(*service._tasks), 10)
    now[0] += timedelta(minutes=31)
    result = await service.add_turn("demo-requester-g3", conversation, "새 공지")
    assert result["turn_id"] != conversation
    assert not any(
        task.interrupts
        for task in (
            await service.graph.aget_state(service._config(conversation))
        ).tasks
    )
    assert len(slack.sent) == 0
    records = [
        json.loads(line)
        for path in audit.directory.glob("chain-*.jsonl")
        for line in path.read_text().splitlines()
    ]
    assert sum(row["event"] == "approval.expired" for row in records) == 1
    await asyncio.wait_for(asyncio.gather(*service._tasks), 10)
    assert len(slack.sent) == 0
    await service.aclose()


@async_test
async def test_cleanup_interrupted_conversation_accepts_new_turn():
    from uuid import uuid4

    saver = InMemorySaver()
    graph = build_graph(FakeChat(reply("합성 답변")), FakeTools(), saver)
    conversation = uuid4().hex
    await graph.aupdate_state(
        {"configurable": {"thread_id": conversation}},
        {
            "task_id": conversation,
            "user_id": "a",
            "status": "running",
            "created_at": "2026-10-08T00:00:00+00:00",
        },
        as_node="__start__",
    )
    service = TaskService(graph)
    await asyncio.wait_for(service.startup_cleanup(), 10)
    assert (await service.get("a", conversation))["status"] == "interrupted"
    result = await service.add_turn("a", conversation, "재시도")
    await asyncio.wait_for(asyncio.gather(*service._tasks), 10)
    assert (await service.get("a", result["turn_id"]))["status"] == "completed"
    await service.aclose()


@async_test
async def test_postgres_failure_does_not_expose_transport(monkeypatch):
    import psycopg
    import pytest

    from govops.api.conversations import PostgresTurnIndex

    async def fail(*args, **kwargs):
        raise RuntimeError("synthetic-private-transport-detail")

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", fail)
    index = PostgresTurnIndex("synthetic")
    with pytest.raises(RuntimeError, match="^turn index DB operation failed$"):
        await asyncio.wait_for(index.conversation_of("absent"), 10)
