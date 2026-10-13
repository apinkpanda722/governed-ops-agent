"""Offline soft-delete contracts through the real, serialized task service."""

import asyncio
import copy
import json
import threading
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, FakeTools, async_test, call, reply
from test_api_conversation_views import client_for, settled

from govops.agent.graph import WITH_WRITES, EffectDeps, build_graph, get_task
from govops.agent.scope import ScopeIndex
from govops.agent.users import UserInfo
from govops.api.conversations import ConversationError, InMemoryTurnIndex
from govops.api.tasks import ApprovalError, TaskService
from govops.audit.log import AuditWriteError, MemoryAuditLog
from govops.guardrails.policy import TeamPolicy
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.resilience import ToolPolicy

OWNER = "demo-requester-g3"
APPROVER = "demo-approver-g3"
OTHER = "demo-other-g3"
REQUEST = "합성 삭제 요청 원문"


def read_service(chat=None, turns=None):
    audit = MemoryAuditLog()
    graph = build_graph(
        chat or FakeChat(reply("첫 합성 답변"), reply("둘째 합성 답변")),
        FakeTools(),
        InMemorySaver(),
        audit=audit,
    )
    return TaskService(graph, audit=audit, turns=turns or InMemoryTurnIndex())


def write_service():
    users = {
        user.label: user
        for user in (
            UserInfo(OWNER, "platform", 6),
            UserInfo(APPROVER, "platform", 4),
            UserInfo(OTHER, "finance", 4),
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
        FakeChat(
            reply(
                calls=[
                    call(
                        "slack_send_message",
                        json.dumps({"channel": "announce", "text": "합성 안내"}),
                    )
                ]
            )
        ),
        tools,
        InMemorySaver(),
        profile=WITH_WRITES,
        effects=effects,
        audit=audit,
        scope=ScopeIndex({}, {}, tuple(users.values())),
        policy=TeamPolicy({"platform": "block"}),
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


def deleted_events(service):
    return [
        record
        for record in service.audit.records
        if record["event"] == "conversation.deleted"
    ]


async def conversation_error(operation, status, code):
    with pytest.raises(ConversationError) as error:
        await operation
    assert (error.value.status_code, error.value.code, str(error.value)) == (
        status,
        code,
        code,
    )


@async_test
async def test_owner_delete_preserves_checkpoints_and_hides_every_entry(monkeypatch):
    service = read_service()
    try:
        ident = await service.start(OWNER, REQUEST)
        await settled(service)
        assert (await service.get(OWNER, ident))["status"] == "completed"
        second = await service.add_turn(OWNER, ident, "둘째 합성 요청")
        await settled(service)
        assert (await service.get(OWNER, second["turn_id"]))["status"] == "completed"
        assert len((await service.conversations(OWNER, 50))["conversations"]) == 1
        state = await get_task(service.graph, ident)
        storage = copy.deepcopy(service.graph.checkpointer.storage)
        before = copy.deepcopy(service.audit.records)
        local_now = datetime(2026, 10, 13, 12, tzinfo=timezone(timedelta(hours=9)))
        service.clock = lambda: local_now
        assert await service.delete_conversation(OWNER, ident) == {
            "conversation_id": ident,
            "deleted": True,
        }
        assert await service.turns.is_deleted(ident)
        assert {row["deleted_at"] for row in service.turns.rows.values()} == {
            local_now.astimezone(UTC).isoformat()
        }
        event = {
            "event": "conversation.deleted",
            "task_id": ident,
            "actor_sub": OWNER,
        }
        assert service.audit.records == [*before, event]
        assert deleted_events(service) == [event]
        assert REQUEST not in repr(service.audit.records) + repr(service.turns.rows)
        assert "둘째 합성 요청" not in repr(service.audit.records)
        assert await get_task(service.graph, ident) == state
        assert service.graph.checkpointer.storage == storage

        async def no_state_read(*args, **kwargs):
            pytest.fail("Deleted entry must be checked before reading graph state")

        monkeypatch.setattr("govops.api.tasks.get_task", no_state_read)
        for user in (OWNER, OTHER):
            await conversation_error(
                service.conversation_view(user, ident), 404, "conversation_not_found"
            )
            await conversation_error(
                service.add_turn(user, ident, "다음"), 404, "conversation_not_found"
            )
            assert await service.progress(user, ident) is None
            for turn in (ident, second["turn_id"]):
                assert await service.get(user, turn) is None
                with pytest.raises(ApprovalError) as error:
                    await service.approve(
                        user, turn, decision="approve", draft_sha256="a" * 64
                    )
                assert error.value.status_code == 404
            assert await service.approvals(user) == {"approvals": []}
            assert await service.conversations(user, 50) == {"conversations": []}
        await conversation_error(
            service.delete_conversation(OWNER, ident), 404, "conversation_not_found"
        )
        assert deleted_events(service) == [event]
    finally:
        await service.aclose()


@pytest.mark.parametrize("target", ["invalid", "upper", "short", "absent", "other"])
@async_test
async def test_unavailable_conversations_share_errors_without_changes(target):
    service = read_service()
    try:
        ident = await service.start(OTHER, REQUEST)
        await settled(service)
        value = {
            "invalid": "not-a-valid-id",
            "upper": "A" * 32,
            "short": "a" * 31,
            "absent": uuid4().hex,
            "other": ident,
        }[target]
        state = await get_task(service.graph, ident)
        audit = copy.deepcopy(service.audit.records)
        for operation in (
            service.delete_conversation(OWNER, value),
            service.conversation_view(OWNER, value),
            service.add_turn(OWNER, value, "다음 합성 요청"),
        ):
            await conversation_error(operation, 404, "conversation_not_found")
        assert await service.progress(OWNER, value) is None
        assert await service.get(OWNER, value) is None
        with pytest.raises(ApprovalError) as error:
            await service.approve(
                OWNER, value, decision="approve", draft_sha256="a" * 64
            )
        if target != "other":
            assert error.value.status_code == 404
        assert await get_task(service.graph, ident) == state
        assert service.audit.records == audit
        assert not await service.turns.is_deleted(ident)
    finally:
        await service.aclose()


@async_test
async def test_running_delete_is_rejected_including_persisted_running_state():
    gate = asyncio.Event()

    class HeldChat(FakeChat):
        async def complete(self, **kwargs):
            await asyncio.wait_for(gate.wait(), 10)
            return await super().complete(**kwargs)

    service = read_service(HeldChat(reply("합성 답변")))
    try:
        ident = await service.start(OWNER, REQUEST)
        await conversation_error(
            service.delete_conversation(OWNER, ident), 409, "turn_in_progress"
        )
        # The execution handle still forbids deletion if checkpoint status lags.
        await service._update(ident, {"status": "completed"})
        await conversation_error(
            service.delete_conversation(OWNER, ident), 409, "turn_in_progress"
        )
        gate.set()
        await settled(service)
        await service._update(ident, {"status": "running"})
        await conversation_error(
            service.delete_conversation(OWNER, ident), 409, "turn_in_progress"
        )
        assert deleted_events(service) == []
        assert not await service.turns.is_deleted(ident)
    finally:
        gate.set()
        await service.aclose()


@pytest.mark.parametrize("interrupt_only", [False, True])
@async_test
async def test_pending_delete_is_rejected_even_if_status_hides_interrupt(
    interrupt_only, monkeypatch
):
    service, slack, _ = write_service()
    try:
        ident = await service.start(OWNER, REQUEST)
        await settled(service)
        assert (await service.get(OWNER, ident))["status"] == "awaiting_approval"
        if interrupt_only:
            # Keep the real interrupt and simulate a stale service state read.
            async def stale_status(graph, conversation_id):
                state = await get_task(graph, conversation_id)
                return {**state, "status": "completed"}

            monkeypatch.setattr("govops.api.tasks.get_task", stale_status)
        assert any(
            task.interrupts
            for task in (await service.graph.aget_state(service._config(ident))).tasks
        )
        await conversation_error(
            service.delete_conversation(OWNER, ident), 409, "approval_pending"
        )
        assert not await service.turns.is_deleted(ident)
        assert deleted_events(service) == []
        assert slack.sent == []
    finally:
        await service.aclose()


@async_test
async def test_expired_approval_closes_before_delete():
    service, slack, now = write_service()
    try:
        ident = await service.start(OWNER, REQUEST)
        await settled(service)
        assert (await service.get(OWNER, ident))["status"] == "awaiting_approval"
        now[0] += timedelta(minutes=31)
        assert (await service.delete_conversation(OWNER, ident))["deleted"] is True
        state = await get_task(service.graph, ident)
        assert state["status"] == "expired"
        assert not any(
            task.interrupts
            for task in (await service.graph.aget_state(service._config(ident))).tasks
        )
        events = [row["event"] for row in service.audit.records]
        assert (
            events.count("approval.expired")
            == events.count("conversation.deleted")
            == 1
        )
        assert events.index("approval.expired") < events.index("conversation.deleted")
        assert slack.sent == []
        assert await service.turns.awaiting() == []
    finally:
        await service.aclose()


@async_test
async def test_delete_blocks_receipt_replay_and_permission_disclosure(monkeypatch):
    service, slack, _ = write_service()
    try:
        ident = await service.start(OWNER, REQUEST)
        await settled(service)
        assert len((await service.approvals(APPROVER))["approvals"]) == 1
        body = {
            "decision": "approve",
            "draft_sha256": (await service.get(OWNER, ident))["draft_sha256"],
        }
        assert (await service.approve(APPROVER, ident, **body))[0] == 202
        await settled(service)
        assert (await service.approve(APPROVER, ident, **body))[0] == 200
        assert (await get_task(service.graph, ident))["approval_receipt"]
        state = await get_task(service.graph, ident)
        outbox = copy.deepcopy(service.effects.outbox.rows)
        sent = copy.deepcopy(slack.sent)
        await service.delete_conversation(OWNER, ident)

        def no_permission_check(*args):
            pytest.fail("Deleted approval must not reach permission or receipt checks")

        monkeypatch.setattr(service, "_decision", no_permission_check)
        client, auth = client_for(service)
        async with client:
            for user in (OWNER, APPROVER, OTHER):
                for target in (ident, uuid4().hex, "invalid"):
                    response = await client.post(
                        f"/tasks/{target}/approval", headers=auth(user), json=body
                    )
                    assert (response.status_code, response.json()) == (
                        404,
                        {"detail": "Task not found"},
                    )
                assert await service.approvals(user) == {"approvals": []}
                assert await service.conversations(user, 50) == {"conversations": []}
        assert await get_task(service.graph, ident) == state
        assert slack.sent == sent and len(sent) == 1
        assert service.effects.outbox.rows == outbox
        assert len(deleted_events(service)) == 1
    finally:
        await service.aclose()


@pytest.mark.parametrize("missing", [False, True])
@async_test
async def test_audit_failure_keeps_conversation_available(missing):
    service = read_service()
    try:
        ident = await service.start(OWNER, REQUEST)
        await settled(service)

        class FailingAudit:
            def append(self, *args, **kwargs):
                raise AuditWriteError("synthetic-private-audit-detail")

        service.audit = None if missing else FailingAudit()
        await conversation_error(
            service.delete_conversation(OWNER, ident), 500, "audit_write_failed"
        )
        assert not await service.turns.is_deleted(ident)
        assert (await service.conversation_view(OWNER, ident))["turn"][
            "status"
        ] == "completed"
        second = await service.add_turn(OWNER, ident, "계속 가능한 합성 요청")
        await settled(service)
        assert (await service.get(OWNER, second["turn_id"]))["status"] == "completed"
    finally:
        await service.aclose()


@async_test
async def test_mark_failure_is_safe_and_follows_threaded_audit_under_locks(monkeypatch):
    service = read_service()
    try:
        ident = await service.start(OWNER, REQUEST)
        await settled(service)
        loop_thread = threading.get_ident()
        append = service.audit.append
        calls = []

        def checked_append(event, **fields):
            assert threading.get_ident() != loop_thread
            assert service._conv_lock(ident).locked()
            assert service._lock(ident).locked()
            calls.append("audit")
            return append(event, **fields)

        async def fail_mark(*args):
            assert len(deleted_events(service)) == 1
            assert service._conv_lock(ident).locked()
            assert service._lock(ident).locked()
            calls.append("mark")
            raise RuntimeError("synthetic-private-index-detail")

        monkeypatch.setattr(service.audit, "append", checked_append)
        monkeypatch.setattr(service.turns, "mark_deleted", fail_mark)
        await conversation_error(
            service.delete_conversation(OWNER, ident), 500, "delete_failed"
        )
        assert calls == ["audit", "mark"]
        assert not await service.turns.is_deleted(ident)
        assert len(deleted_events(service)) == 1
        assert (await service.conversation_view(OWNER, ident))["turn"][
            "status"
        ] == "completed"
    finally:
        await service.aclose()


@async_test
async def test_approvals_defensively_skip_deleted_candidates(monkeypatch):
    service, slack, now = write_service()
    try:
        ident = await service.start(OWNER, REQUEST)
        await settled(service)
        assert len((await service.approvals(APPROVER))["approvals"]) == 1
        await service.turns.mark_deleted(ident, OWNER, now[0].isoformat())

        async def stale_candidates():
            return [(ident, ident)]

        async def no_state_read(*args):
            pytest.fail("Deleted approval candidates must not read graph state")

        monkeypatch.setattr(service.turns, "awaiting", stale_candidates)
        monkeypatch.setattr("govops.api.tasks.get_task", no_state_read)
        assert await service.approvals(APPROVER) == {"approvals": []}
        assert slack.sent == []
    finally:
        await service.aclose()


@pytest.mark.parametrize("launch_order", [("turn", "delete"), ("delete", "turn")])
@async_test
async def test_concurrent_add_turn_and_delete_have_only_serialized_outcomes(
    launch_order,
):
    gate = asyncio.Event()
    start = asyncio.Event()

    class HeldSecondChat(FakeChat):
        async def complete(self, **kwargs):
            if self.calls:
                await asyncio.wait_for(gate.wait(), 10)
            return await super().complete(**kwargs)

    service = read_service(HeldSecondChat(reply("첫 답변"), reply("둘째 답변")))
    try:
        ident = await service.start(OWNER, REQUEST)
        await settled(service)

        async def action(kind):
            await asyncio.wait_for(start.wait(), 10)
            try:
                if kind == "delete":
                    await service.delete_conversation(OWNER, ident)
                    return kind, "deleted"
                await service.add_turn(OWNER, ident, "동시 합성 요청")
                return kind, "accepted"
            except ConversationError as error:
                return kind, (error.status_code, error.code)

        executions = [asyncio.create_task(action(kind)) for kind in launch_order]
        start.set()
        results = frozenset(await asyncio.wait_for(asyncio.gather(*executions), 10))
        assert results in {
            frozenset(
                {("delete", "deleted"), ("turn", (404, "conversation_not_found"))}
            ),
            frozenset({("turn", "accepted"), ("delete", (409, "turn_in_progress"))}),
        }
        deleted = ("delete", "deleted") in results
        assert await service.turns.is_deleted(ident) is deleted
        assert len(deleted_events(service)) == int(deleted)
        if deleted:
            await conversation_error(
                service.add_turn(OWNER, ident, "삭제 이후"),
                404,
                "conversation_not_found",
            )
        else:
            assert len(service.turns.rows) == 2
        gate.set()
        await settled(service)
    finally:
        gate.set()
        await service.aclose()
