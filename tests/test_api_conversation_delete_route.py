"""Registered R1–R6 × S1–S4 and E1–E8 probes through the server lifespan."""

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, FakeTools, async_test, call, reply
from test_api_conversation_views import settled

import govops.api.__main__ as entry
from govops.agent.approval import Decision
from govops.agent.graph import WITH_WRITES, EffectDeps, build_graph, get_task
from govops.agent.scope import ScopeIndex
from govops.agent.users import UserInfo
from govops.api.conversations import InMemoryTurnIndex
from govops.api.tasks import TaskService
from govops.audit.log import MemoryAuditLog
from govops.guardrails.policy import TeamPolicy
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.resilience import ToolPolicy

OWNER = "demo-requester-g3"
APPROVER = "demo-approver-g3"
PROBE = "demo-other-g3"
APPROVAL = {"decision": "approve", "draft_sha256": "a" * 64}
ROUTES = [
    pytest.param("GET", "/conversations/{id}", None, id="R1"),
    pytest.param("GET", "/conversations/{id}/events", None, id="R2"),
    pytest.param(
        "POST", "/conversations/{id}/turns", {"request": "합성 다음 요청"}, id="R3"
    ),
    pytest.param("DELETE", "/conversations/{id}", None, id="R4"),
    pytest.param("GET", "/tasks/{id}", None, id="R5"),
    pytest.param("POST", "/tasks/{id}/approval", APPROVAL, id="R6"),
]


def draft_reply(channel="announce"):
    return reply(
        calls=[
            call(
                "slack_send_message",
                json.dumps({"channel": channel, "text": "합성 안내"}),
            )
        ]
    )


def task_service(chat):
    users = {
        user.label: user
        for user in (
            UserInfo(OWNER, "platform", 6),
            UserInfo(APPROVER, "platform", 4),
            UserInfo(PROBE, "finance", 4),
        )
    }
    now = [datetime(2026, 10, 13, tzinfo=UTC)]
    audit = MemoryAuditLog()
    tools = FakeTools()
    tools.policy = ToolPolicy()

    class Slack:
        def __init__(self):
            self.sent = []

        async def send_message(self, user_id, **args):
            self.sent.append((user_id, args))
            return {"message": {"message_id": "message-1"}}

    slack = Slack()
    effects = EffectDeps(
        SimpleNamespace(add_comment=None, create_followup=None),
        slack,
        InMemoryOutboxStore(),
    )
    graph = build_graph(
        chat,
        tools,
        InMemorySaver(),
        profile=WITH_WRITES,
        effects=effects,
        audit=audit,
        scope=ScopeIndex({}, {}, tuple(users.values())),
        policy=TeamPolicy({"platform": "block", "finance": "block"}),
        users=users,
        clock=lambda: now[0],
    )
    return (
        TaskService(
            graph,
            users=users,
            effects=effects,
            audit=audit,
            policy=tools.policy,
            turns=InMemoryTurnIndex(),
        ),
        slack,
        now,
    )


@asynccontextmanager
async def server(monkeypatch, service):
    @asynccontextmanager
    async def open_service():
        try:
            yield service
        finally:
            await service.aclose()

    monkeypatch.setenv("DEMO_LOGIN", "false")
    monkeypatch.setattr(entry, "load_users", lambda: frozenset(service.users))
    monkeypatch.setattr(entry, "open_task_service", open_service)
    app = entry.build_server_config(0).app
    placeholder = app.state.task_service
    assert placeholder is not service and placeholder.graph is None

    def auth(user):
        return {"Authorization": "Bearer " + app.state.sessions.issue(user)[0]}

    async def no_server_error(response):
        assert response.status_code != 500

    async with app.router.lifespan_context(app):
        assert app.state.task_service is service
        assert app.state.audit is service.audit
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://127.0.0.1",
            timeout=5,
            event_hooks={"response": [no_server_error]},
        ) as client:
            yield app, client, auth


def expect(response, status, body):
    assert response.status_code == status
    assert response.json() == body
    assert response.headers["content-type"] == "application/json"


def deletion_events(service):
    return [
        row for row in service.audit.records if row["event"] == "conversation.deleted"
    ]


async def snapshot(service, slack, *ids):
    return copy.deepcopy(
        (
            [await get_task(service.graph, ident) for ident in ids],
            # InMemorySaver's defaultdict creates empty buckets on absent-ID reads.
            # Compare persisted checkpoints, including every nonempty thread/namespace.
            {
                ident: namespaces
                for ident, namespaces in service.graph.checkpointer.storage.items()
                if any(namespaces.values())
            },
            service.turns.rows,
            service.audit.records,
            slack.sent,
            service.effects.outbox.rows,
        )
    )


async def start_completed(service, user):
    ident = await service.start(user, "합성 대화 요청")
    await settled(service)
    assert (await get_task(service.graph, ident))["status"] == "completed"
    return ident


@pytest.mark.parametrize("method,path,body", ROUTES)
@async_test
async def test_unavailable_targets_are_indistinguishable(
    monkeypatch, method, path, body
):
    service, slack, _ = task_service(
        FakeChat(draft_reply(), reply("첫 합성 답변"), reply("둘째 합성 답변"))
    )
    async with server(monkeypatch, service) as (app, client, auth):
        foreign = await service.start(OWNER, "합성 공지 요청")
        await settled(service)
        state = await get_task(service.graph, foreign)
        assert state["status"] == "awaiting_approval"
        await service.approve(
            APPROVER, foreign, decision="approve", draft_sha256=state["draft_sha256"]
        )
        await settled(service)
        foreign_state = await get_task(service.graph, foreign)
        assert foreign_state["status"] == "completed"
        assert service._decision(foreign_state, PROBE) != Decision.ALLOW
        assert len(slack.sent) == 1

        deleted = await start_completed(service, PROBE)
        second = await service.add_turn(PROBE, deleted, "둘째 합성 요청")
        await settled(service)
        assert (await get_task(service.graph, deleted))["status"] == "completed"
        expect(
            await client.delete(f"/conversations/{deleted}", headers=auth(PROBE)),
            200,
            {"conversation_id": deleted, "deleted": True},
        )
        before = await snapshot(service, slack, foreign, deleted)

        if path.endswith("/events"):

            def no_stream(*args, **kwargs):
                pytest.fail("Unavailable conversations must not open SSE")

            monkeypatch.setattr("govops.api.app.StreamingResponse", no_stream)

        targets = [
            ("S1", foreign),
            ("S2", uuid4().hex),
            ("S3-first", deleted),
            ("S3-second", second["turn_id"]),
            ("S4-characters", "not-a-valid-id"),
            ("S4-upper", "A" * 32),
            ("S4-length", "a" * 31),
        ]
        identical = []
        for scenario, target in targets:
            response = await client.request(
                method, path.format(id=target), headers=auth(PROBE), json=body
            )
            if path.endswith("/approval") and scenario == "S1":
                # R6 × S1 keeps the existing approval policy; only no mutation is required.
                assert response.status_code != 200
            else:
                expect(
                    response,
                    404,
                    {
                        "detail": "Task not found"
                        if path.startswith("/tasks")
                        else "conversation_not_found"
                    },
                )
                identical.append((response.status_code, response.content))
            assert app.state.sse_active_streams == 0
            expect(
                await client.get("/approvals", headers=auth(PROBE)),
                200,
                {"approvals": []},
            )
            assert await snapshot(service, slack, foreign, deleted) == before
        assert len(identical) == (6 if path.endswith("/approval") else 7)
        assert len(set(identical)) == 1


@pytest.mark.parametrize("method,path,body", ROUTES)
@async_test
async def test_e3_missing_session(monkeypatch, method, path, body):
    service, slack, _ = task_service(FakeChat(reply("합성 답변")))
    async with server(monkeypatch, service) as (app, client, _):
        ident = await start_completed(service, OWNER)
        state = await get_task(service.graph, ident)
        expect(
            await client.request(method, path.format(id=ident), json=body),
            401,
            {"detail": "Authentication required"},
        )
        expect(
            await client.get("/approvals"), 401, {"detail": "Authentication required"}
        )
        assert await get_task(service.graph, ident) == state
        assert not await service.turns.is_deleted(ident)
        assert slack.sent == [] and service.effects.outbox.rows == {}
        assert deletion_events(service) == []
        assert app.state.sse_active_streams == 0


@async_test
async def test_e1_pending_approval_cannot_be_deleted(monkeypatch):
    service, slack, _ = task_service(FakeChat(draft_reply()))
    async with server(monkeypatch, service) as (_, client, auth):
        ident = await service.start(OWNER, "합성 승인 대기 요청")
        await settled(service)
        before = await snapshot(service, slack, ident)
        assert before[0][0]["status"] == "awaiting_approval"
        expect(
            await client.delete(f"/conversations/{ident}", headers=auth(OWNER)),
            409,
            {"detail": "approval_pending"},
        )
        assert await snapshot(service, slack, ident) == before
        assert not await service.turns.is_deleted(ident)
        assert deletion_events(service) == []


@async_test
async def test_e2_running_turn_cannot_be_deleted(monkeypatch):
    gate = asyncio.Event()

    class HeldChat(FakeChat):
        async def complete(self, **kwargs):
            await asyncio.wait_for(gate.wait(), 10)
            return await super().complete(**kwargs)

    service, slack, _ = task_service(HeldChat(reply("합성 답변")))
    async with server(monkeypatch, service) as (_, client, auth):
        ident = await service.start(OWNER, "합성 실행 중 요청")
        assert (await get_task(service.graph, ident))["status"] == "running"
        expect(
            await client.delete(f"/conversations/{ident}", headers=auth(OWNER)),
            409,
            {"detail": "turn_in_progress"},
        )
        assert not await service.turns.is_deleted(ident)
        assert deletion_events(service) == [] and slack.sent == []
        gate.set()
        await settled(service)


@async_test
async def test_e4_e6_e7_owner_delete_and_second_turn_id(monkeypatch):
    service, slack, _ = task_service(FakeChat(reply("첫 답변"), reply("둘째 답변")))
    async with server(monkeypatch, service) as (_, client, auth):
        ident = await start_completed(service, OWNER)
        second = await service.add_turn(OWNER, ident, "둘째 합성 요청")
        await settled(service)
        assert second["turn_id"] != ident
        before = await snapshot(service, slack, ident)
        expect(
            await client.delete(
                f"/conversations/{second['turn_id']}", headers=auth(OWNER)
            ),
            404,
            {"detail": "conversation_not_found"},
        )
        assert await snapshot(service, slack, ident) == before
        assert not await service.turns.is_deleted(ident)
        expect(
            await client.delete(f"/conversations/{ident}", headers=auth(OWNER)),
            200,
            {"conversation_id": ident, "deleted": True},
        )
        event = {"event": "conversation.deleted", "task_id": ident, "actor_sub": OWNER}
        assert deletion_events(service) == [event]
        assert service.audit.records == [*before[3], event]
        assert await service.turns.is_deleted(ident)
        after = await snapshot(service, slack, ident)
        expect(
            await client.delete(f"/conversations/{ident}", headers=auth(OWNER)),
            404,
            {"detail": "conversation_not_found"},
        )
        assert await snapshot(service, slack, ident) == after
        assert deletion_events(service) == [event]
        expect(
            await client.get("/conversations", headers=auth(OWNER)),
            200,
            {"conversations": []},
        )
        for user in (OWNER, APPROVER):
            expect(
                await client.get("/approvals", headers=auth(user)),
                200,
                {"approvals": []},
            )


@async_test
async def test_e5_e8_expired_approvals_close_before_delete_and_leave_lists(monkeypatch):
    service, slack, now = task_service(
        FakeChat(draft_reply("team-platform"), draft_reply())
    )
    async with server(monkeypatch, service) as (_, client, auth):
        ids = []
        for _ in range(2):
            ids.append(await service.start(OWNER, "합성 승인 요청"))
            await settled(service)
            assert (await get_task(service.graph, ids[-1]))[
                "status"
            ] == "awaiting_approval"
        for user, ident in zip((OWNER, APPROVER), ids, strict=True):
            response = await client.get("/approvals", headers=auth(user))
            assert response.status_code == 200
            assert [row["task_id"] for row in response.json()["approvals"]] == [ident]
        response = await client.get("/conversations", headers=auth(OWNER))
        assert response.status_code == 200
        assert {
            row["conversation_id"] for row in response.json()["conversations"]
        } == set(ids)

        now[0] += timedelta(minutes=31)
        for ident in ids:
            expect(
                await client.delete(f"/conversations/{ident}", headers=auth(OWNER)),
                200,
                {"conversation_id": ident, "deleted": True},
            )
            assert (await get_task(service.graph, ident))["status"] == "expired"
            assert not any(
                task.interrupts
                for task in (
                    await service.graph.aget_state(service._config(ident))
                ).tasks
            )
            events = [
                row["event"]
                for row in service.audit.records
                if row.get("task_id") == ident
            ]
            assert (
                events.count("approval.expired")
                == events.count("conversation.deleted")
                == 1
            )
            assert events.index("approval.expired") < events.index(
                "conversation.deleted"
            )
        assert slack.sent == [] and service.effects.outbox.rows == {}
        expect(
            await client.get("/conversations", headers=auth(OWNER)),
            200,
            {"conversations": []},
        )
        for user in (OWNER, APPROVER):
            expect(
                await client.get("/approvals", headers=auth(user)),
                200,
                {"approvals": []},
            )
