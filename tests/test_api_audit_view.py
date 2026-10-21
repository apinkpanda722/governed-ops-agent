"""Synthetic, offline service probes for the read-only audit boundary."""

import asyncio
import copy
import json
import re
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, FakeTools, async_test, call, reply
from test_api_conversation_views import settled

from govops.agent.approval import Decision
from govops.agent.graph import WITH_WRITES, EffectDeps, build_graph, get_task
from govops.agent.scope import ScopeIndex
from govops.agent.users import UserInfo
from govops.api.conversations import ConversationError
from govops.api.tasks import TaskService
from govops.audit import records
from govops.audit.crypto import canonical_json, kid_of, record_hash
from govops.audit.log import AuditLog, MemoryAuditLog
from govops.guardrails.policy import TeamPolicy
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.resilience import ToolPolicy

OWNER = "demo-requester-g3"
APPROVER = "demo-approver-g3"
OTHER = "demo-other-g3"
BASE = {"seq", "ts", "event", "actor", "hash12"}
OPTIONAL = {
    "tool",
    "decision",
    "check_result",
    "reason",
    "code",
    "mock_id",
    "fallback",
    "cost_usd",
    "draft_hash12",
    "tool_name",
    "status",
    "result_count",
}


def draft_reply():
    return reply(
        calls=[
            call(
                "slack_send_message",
                json.dumps(
                    {
                        "channel": "announce",
                        "text": "합성 안내",
                    }
                ),
            )
        ]
    )


@async_test
async def test_owner_http_tool_call_projection_hides_hashes_and_key(
    service_factory, monkeypatch
):
    import secrets

    from test_api_conversation_views import client_for

    from govops.audit.argmac import load_args_mac
    from govops.audit.toolcall import ToolCallAuditor

    key_hex = secrets.token_hex(32)
    monkeypatch.setenv("AUDIT_ARGS_HMAC_KEY", key_hex)
    service, _, _ = service_factory()
    client, auth = client_for(service)
    try:
        ident = await service.start(OWNER, "합성 요청")
        await settled(service)
        seq = ToolCallAuditor(service.audit, load_args_mac()).record(
            task_id=ident,
            actor_sub=OWNER,
            tool_name="jira_get_ticket",
            tool_call_id="synthetic-call",
            arguments='{"ticket_id":"SYN-1"}',
            status="ok",
            result_count=1,
        )
        row = next(r for r in chain_rows(service.audit.directory) if r["seq"] == seq)
        before = chain_snapshot(service.audit.directory)
        async with client:
            response = await client.get(
                f"/conversations/{ident}/audit", headers=auth(OWNER)
            )
        assert response.status_code == 200
        value = response.json()
        assert value["conversation_id"] == ident
        assert value["truncated"] is False
        assert len(value["turns"]) == 1
        events = [e for e in value["turns"][0]["events"] if e["event"] == "tool.call"]
        assert events == [
            {
                "seq": seq,
                "ts": row["ts"],
                "event": "tool.call",
                "actor": OWNER,
                "hash12": row["hash"][:12],
                "tool_name": "jira_get_ticket",
                "status": "ok",
                "result_count": 1,
            }
        ]
        assert key_hex not in response.text
        for key in ("args_hmac", "args_hmac_kid", "tool_call_sha256"):
            assert key not in response.text
            assert row[key] not in response.text
        assert not re.search(r"[0-9a-f]{64}|[0-9a-f]{128}", response.text)
        assert chain_snapshot(service.audit.directory) == before
    finally:
        await client.aclose()
        await service.aclose()


def chain_snapshot(directory):
    return {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in directory.glob("chain-*.jsonl")
    }


def chain_rows(directory):
    return [
        json.loads(line)
        for path in sorted(directory.glob("chain-*.jsonl"))
        for line in path.read_bytes().splitlines()
    ]


def write_rows(path, rows):
    path.write_text("".join(canonical_json(row) + "\n" for row in rows))


async def error_is(operation, status, code):
    with pytest.raises(ConversationError) as caught:
        await operation
    assert (caught.value.status_code, caught.value.code, str(caught.value)) == (
        status,
        code,
        code,
    )
    assert caught.value.__cause__ is None


@pytest.fixture
def service_factory(tmp_path, monkeypatch):
    monkeypatch.setattr(records, "ROOT", tmp_path)
    monkeypatch.setenv("AUDIT_ANCHORS_PENDING_FILE", str(tmp_path / "pending.jsonl"))
    users = {
        u.label: u
        for u in (
            UserInfo(OWNER, "platform", 6),
            UserInfo(APPROVER, "platform", 4),
            UserInfo(OTHER, "finance", 4),
        )
    }
    services = []

    def create(chat=None):
        now = [datetime(2026, 10, 16, tzinfo=UTC)]
        audit = AuditLog(tmp_path / f"audit-{len(services)}")

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
        tools = FakeTools()
        tools.policy = ToolPolicy()
        graph = build_graph(
            chat or FakeChat(reply("첫 합성 답변"), reply("둘째 합성 답변")),
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
        service = TaskService(
            graph,
            audit=audit,
            users=users,
            effects=effects,
            policy=tools.policy,
        )
        services.append(service)
        return service, slack, now

    return create


@pytest.mark.parametrize(
    "target",
    [
        "S1-other",
        "S2-absent",
        "S3-deleted",
        "S4-invalid",
        "S4-upper",
        "S4-short",
        "S5-approver",
        "S6-pruned",
        "S7-second-turn",
    ],
)
@async_test
async def test_a1_unavailable_targets_have_identical_errors(
    service_factory,
    monkeypatch,
    target,
):
    service, _, _ = service_factory(
        FakeChat(draft_reply()) if target == "S5-approver" else None
    )
    try:
        ident = await service.start(OWNER, "합성 요청")
        await settled(service)
        service.audit.append("side_effect.draft", task_id=ident, actor_sub=OWNER)
        user, value = OWNER, ident
        if target == "S1-other":
            user = OTHER
        elif target == "S2-absent":
            value = uuid4().hex
        elif target == "S3-deleted":
            await service.delete_conversation(OWNER, ident)
        elif target.startswith("S4"):
            value = {
                "S4-invalid": "not-a-valid-id",
                "S4-upper": "A" * 32,
                "S4-short": "a" * 31,
            }[target]
        elif target == "S5-approver":
            state = await get_task(service.graph, ident)
            assert state["status"] == "awaiting_approval"
            assert service._decision(state, APPROVER) == Decision.ALLOW
            user = APPROVER
        elif target == "S6-pruned":
            assert await service.turns.delete_conversations([ident]) == 1
        else:
            value = (await service.add_turn(OWNER, ident, "둘째 합성 요청"))["turn_id"]
            await settled(service)
        before = chain_snapshot(service.audit.directory)
        assert before
        for method in ("_expire", "_lock"):
            monkeypatch.setattr(
                service, method, Mock(side_effect=AssertionError(method))
            )
        monkeypatch.setattr(
            "govops.api.tasks.get_task", Mock(side_effect=AssertionError("get_task"))
        )
        await error_is(service.audit_view(user, value), 404, "conversation_not_found")
        assert chain_snapshot(service.audit.directory) == before
        # Authorization still wins when the audit backend is unavailable.
        service.audit = MemoryAuditLog()
        await error_is(service.audit_view(user, value), 404, "conversation_not_found")
    finally:
        await service.aclose()


@async_test
async def test_owner_projection_separates_turns_and_exposes_approver(service_factory):
    service, slack, _ = service_factory(
        FakeChat(draft_reply(), reply("둘째 합성 답변"), reply("타인 합성 답변"))
    )
    try:
        ident = await service.start(OWNER, "합성 공지")
        await settled(service)
        state = await get_task(service.graph, ident)
        await service.approve(
            APPROVER, ident, decision="approve", draft_sha256=state["draft_sha256"]
        )
        await settled(service)
        second = (await service.add_turn(OWNER, ident, "둘째 합성 요청"))["turn_id"]
        await settled(service)
        service.audit.append(
            "side_effect.draft", task_id=second, actor_sub=OWNER, draft_hash="b" * 64
        )
        other = await service.start(OTHER, "타인 합성 요청")
        await settled(service)
        service.audit.append("side_effect.draft", task_id=other, actor_sub=OTHER)
        rows = chain_rows(service.audit.directory)
        before = chain_snapshot(service.audit.directory)
        value = await service.audit_view(OWNER, ident)
        assert set(value) == {"conversation_id", "turns", "truncated"}
        assert value["conversation_id"] == ident and value["truncated"] is False
        assert [(t["turn_id"], t["turn_index"]) for t in value["turns"]] == [
            (ident, 0),
            (second, 1),
        ]
        for turn in value["turns"]:
            assert set(turn) == {"turn_id", "turn_index", "events"}
            assert turn["events"]
            assert {e["seq"] for e in turn["events"]} == {
                r["seq"] for r in rows if r["task_id"] == turn["turn_id"]
            }
            assert all(BASE <= set(e) <= BASE | OPTIONAL for e in turn["events"])
        events = [e for t in value["turns"] for e in t["events"]]
        assert any(
            e["event"] == "approval.approved" and e["actor"] == APPROVER for e in events
        )
        encoded = json.dumps(value)
        assert not re.search(r"[0-9a-f]{64}|[0-9a-f]{128}", encoded)
        for forbidden in (
            "kid",
            "prev_hash",
            "input_sha256",
            "tool_call_sha256",
            "deployment_id",
            "Authorization",
            "synthetic-private-token",
            str(service.audit.directory),
            other,
        ):
            assert forbidden not in encoded
        assert len(slack.sent) == 1
        assert chain_snapshot(service.audit.directory) == before
    finally:
        await service.aclose()


@async_test
async def test_untrusted_fields_and_values_never_escape(service_factory):
    service, _, _ = service_factory()
    try:
        ident = await service.start(OWNER, "합成 요청")
        await settled(service)
        service.audit.append(
            "side_effect.draft",
            task_id=ident,
            actor_sub=OWNER,
            tool="slack_send_message",
        )
        path = next(service.audit.directory.glob("chain-*.jsonl"))
        row = chain_rows(service.audit.directory)[-1]
        bad = {**row, "text": "synthetic-private-token", "body": "Authorization"}
        invalid_values = {
            **row,
            "tool": 1,
            "decision": True,
            "check_result": 5,
            "reason": None,
            "mock_id": "a" * 64,
            "draft_hash": True,
        }
        write_rows(path, [row, bad, invalid_values])
        events = (await service.audit_view(OWNER, ident))["turns"][0]["events"]
        assert len(events) == 2
        assert set(events[0]) == BASE | {"tool"}
        assert set(events[1]) == BASE
        assert all(
            word not in json.dumps(events)
            for word in ("text", "body", "Authorization", "synthetic-private-token")
        )
    finally:
        await service.aclose()


@pytest.mark.parametrize("status", ["awaiting_approval", "running", "completed"])
@async_test
async def test_audit_view_is_read_only_even_after_approval_expiry(
    service_factory,
    monkeypatch,
    status,
):
    gate = asyncio.Event()
    entered = asyncio.Event()

    class HeldChat:
        async def complete(self, **kwargs):
            entered.set()
            await asyncio.wait_for(gate.wait(), 10)
            return reply("합성 답변")

    chat = (
        HeldChat()
        if status == "running"
        else FakeChat(
            draft_reply() if status == "awaiting_approval" else reply("합성 답변")
        )
    )
    service, slack, now = service_factory(chat)
    try:
        ident = await service.start(OWNER, "합성 요청")
        if status == "running":
            await asyncio.wait_for(entered.wait(), 10)
        else:
            await settled(service)
        service.audit.append("side_effect.draft", task_id=ident, actor_sub=OWNER)
        now[0] += timedelta(minutes=31)
        state = await get_task(service.graph, ident)
        assert state["status"] == status
        if status == "awaiting_approval":
            assert now[0] > datetime.fromisoformat(state["expires_at"])
        before = copy.deepcopy(
            (
                state,
                service.turns.rows,
                service.graph.checkpointer.storage,
                service.effects.outbox.rows,
                slack.sent,
            )
        )
        chain = chain_snapshot(service.audit.directory)
        loop_thread = threading.get_ident()
        read = records.read_events
        deleted, turn_ids = service.turns.is_deleted, service.turns.turn_ids_of
        calls = []

        async def checked_deleted(conversation_id):
            assert service._conv_lock(ident).locked()
            calls.append("deleted")
            return await deleted(conversation_id)

        async def checked_turns(conversation_id, user_id):
            assert service._conv_lock(ident).locked()
            calls.append("turns")
            return await turn_ids(conversation_id, user_id)

        def checked_read(directory, ids):
            assert threading.get_ident() != loop_thread
            assert not service._conv_lock(ident).locked()
            assert ids == [ident]
            calls.append("read")
            return read(directory, ids)

        with monkeypatch.context() as guarded:
            guarded.setattr(service.turns, "is_deleted", checked_deleted)
            guarded.setattr(service.turns, "turn_ids_of", checked_turns)
            guarded.setattr(records, "read_events", checked_read)
            for obj, name in (
                (service, "_expire"),
                (service, "_lock"),
                (service.turns, "set_awaiting"),
            ):
                guarded.setattr(obj, name, Mock(side_effect=AssertionError(name)))
            guarded.setattr(
                "govops.api.tasks.get_task",
                Mock(side_effect=AssertionError("get_task")),
            )
            assert (await service.audit_view(OWNER, ident))["turns"][0]["events"]
        assert calls == ["deleted", "turns", "read"]
        assert (
            await get_task(service.graph, ident),
            service.turns.rows,
            service.graph.checkpointer.storage,
            service.effects.outbox.rows,
            slack.sent,
        ) == before
        assert chain_snapshot(service.audit.directory) == chain
    finally:
        gate.set()
        await settled(service)
        await service.aclose()


@pytest.mark.parametrize("backend", [None, MemoryAuditLog()])
@async_test
async def test_missing_audit_directory_is_unavailable(service_factory, backend):
    service, _, _ = service_factory()
    try:
        ident = await service.start(OWNER, "합성 요청")
        await settled(service)
        service.audit = backend
        await error_is(service.audit_view(OWNER, ident), 503, "audit_unavailable")
        await error_is(service.audit_verify(), 503, "audit_unavailable")
    finally:
        await service.aclose()


@async_test
async def test_reader_unavailable_is_a_fixed_service_error(service_factory):
    service, _, _ = service_factory()
    try:
        ident = await service.start(OWNER, "합성 요청")
        await settled(service)
        service.audit.directory = service.audit.directory / "missing"
        await error_is(service.audit_view(OWNER, ident), 503, "audit_unavailable")
        await error_is(service.audit_verify(), 503, "audit_unavailable")
    finally:
        await service.aclose()


@pytest.mark.parametrize("count", [0, 501])
@async_test
async def test_empty_turns_and_conversation_wide_truncation(service_factory, count):
    service, _, _ = service_factory()
    ident, second = "1" * 32, "2" * 32
    for turn_id, index in ((second, 1), (ident, 0)):
        await service.turns.add(
            turn_id=turn_id,
            conversation_id=ident,
            owner_sub=OWNER,
            turn_index=index,
            created_at="2026-10-16T00:00:00+00:00",
        )
    for index in range(count):
        service.audit.append(
            "side_effect.draft",
            task_id=ident if index < 500 else second,
            actor_sub=OWNER,
        )
    value = await service.audit_view(OWNER, ident)
    assert value["truncated"] is (count > 500)
    assert [(t["turn_id"], t["turn_index"]) for t in value["turns"]] == [
        (ident, 0),
        (second, 1),
    ]
    assert [len(t["events"]) for t in value["turns"]] == [min(count, 500), 0]


@pytest.fixture
def verify_chain(service_factory, tmp_path):
    service, _, _ = service_factory()
    for _ in range(3):
        service.audit.append(
            "side_effect.draft",
            task_id="1" * 32,
            actor_sub=OWNER,
            tool="slack_send_message",
        )
    service.audit.anchor("synthetic", repo_root=tmp_path)
    path = next(service.audit.directory.glob("chain-*.jsonl"))
    return service, path, chain_rows(service.audit.directory)


@async_test
async def test_verify_summary_contract_is_threaded_and_uses_no_conversation_lock(
    verify_chain,
    monkeypatch,
):
    service, _, _ = verify_chain
    before = chain_snapshot(service.audit.directory)
    verify = records.verify_summary
    loop_thread = threading.get_ident()

    def checked(directory):
        assert threading.get_ident() != loop_thread
        return verify(directory)

    monkeypatch.setattr(records, "verify_summary", checked)
    monkeypatch.setattr(
        service, "_conv_lock", Mock(side_effect=AssertionError("conversation lock"))
    )
    value = await service.audit_verify()
    assert set(value) == {
        "status",
        "checked_at",
        "chain",
        "anchors",
        "legacy_unverified",
        "known_limit",
    }
    assert value["status"] == "pass"
    assert value["chain"] == {
        "records": 5,
        "files": 1,
        "anchors_checked": 1,
        "problems": [],
    }
    assert value["anchors"] == {"committed": 0, "pending": 1}
    assert value["legacy_unverified"] == {"files": 0, "lines": 0}
    assert value["known_limit"] == "tail_after_last_anchor_not_detected"
    assert datetime.fromisoformat(value["checked_at"]).utcoffset() == timedelta(0)
    assert chain_snapshot(service.audit.directory) == before


@pytest.mark.parametrize(
    ("kind", "codes"),
    [
        ("modify", {"hash_mismatch"}),
        ("delete", {"seq_gap"}),
        ("reorder", {"seq_order", "prev_hash_mismatch"}),
        ("tail", {"tail_truncated"}),
        ("key", {"kid_unknown"}),
    ],
)
@async_test
async def test_verify_detects_five_tampering_cases(verify_chain, kind, codes):
    service, path, rows = verify_chain
    if kind == "modify":
        rows[1]["tool"] = "jira_add_comment"
    elif kind == "delete":
        del rows[2]
    elif kind == "reorder":
        rows[1], rows[2] = rows[2], rows[1]
    elif kind == "tail":
        rows = rows[:-1]
    else:
        key = Ed25519PrivateKey.generate()
        previous = "0" * 64
        for row in rows:
            row["kid"], row["prev_hash"] = kid_of(key.public_key()), previous
            row["hash"] = record_hash(
                {k: v for k, v in row.items() if k not in {"hash", "sig"}}
            )
            row["sig"] = key.sign(row["hash"].encode("ascii")).hex()
            previous = row["hash"]
    write_rows(path, rows)
    before = chain_snapshot(service.audit.directory)
    value = await service.audit_verify()
    assert value["status"] == "fail"
    problems = value["chain"]["problems"]
    assert problems and codes & {p["code"] for p in problems}
    assert all(set(p) <= {"code", "seq"} for p in problems)
    encoded = json.dumps(value)
    assert not re.search(r"[0-9a-f]{64}|[0-9a-f]{128}", encoded)
    assert str(path) not in encoded and rows[0]["kid"] not in encoded
    assert chain_snapshot(service.audit.directory) == before


@async_test
async def test_verify_cache_is_lazy_single_flight_and_per_service(
    verify_chain,
    monkeypatch,
):
    service, _, _ = verify_chain
    verify = records.verify_summary
    entered, release = threading.Event(), threading.Event()

    def held(directory):
        entered.set()
        assert release.wait(timeout=10)
        return verify(directory)

    compute = Mock(side_effect=held)
    factory = Mock(wraps=records.VerifySummaryCache)
    monkeypatch.setattr(records, "verify_summary", compute)
    monkeypatch.setattr(records, "VerifySummaryCache", factory)
    requests = [asyncio.create_task(service.audit_verify()) for _ in range(6)]
    try:
        assert await asyncio.to_thread(entered.wait, 5)
    finally:
        release.set()
    values = await asyncio.wait_for(asyncio.gather(*requests), 10)
    assert len(values) == 6 and all(v == values[0] for v in values)
    assert await service.audit_verify() == values[0]
    assert factory.call_count == compute.call_count == 1
    fresh = TaskService(None, audit=service.audit)
    assert factory.call_count == 1
    assert (await fresh.audit_verify())["status"] == "pass"
    assert factory.call_count == compute.call_count == 2
