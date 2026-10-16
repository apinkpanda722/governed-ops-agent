"""Registered M7 audit HTTP probes through build_server_config and lifespan."""

import asyncio
import copy
import json
import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
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
from govops.audit import records
from govops.audit.crypto import canonical_json, kid_of, record_hash
from govops.audit.log import AuditLog
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


@pytest.fixture
def service_factory(tmp_path, monkeypatch):
    # Both committed and pending anchors resolve inside the synthetic environment.
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

    def create(chat=None):
        now = [datetime(2026, 10, 16, tzinfo=UTC)]
        audit = AuditLog(tmp_path / uuid4().hex)
        slack = SimpleNamespace(sent=[])

        async def send_message(user_id, **args):
            slack.sent.append((user_id, args))
            return {"message": {"message_id": "message-1"}}

        slack.send_message = send_message
        effects = EffectDeps(
            SimpleNamespace(add_comment=None, create_followup=None),
            slack,
            InMemoryOutboxStore(),
        )
        tools = FakeTools()
        tools.policy = ToolPolicy()
        graph = build_graph(
            chat or FakeChat(reply("합성 답변")),
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
                audit=audit,
                users=users,
                effects=effects,
                policy=tools.policy,
                turns=InMemoryTurnIndex(),
            ),
            slack,
            now,
        )

    return create


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
        assert app.state.task_service is service and app.state.audit is service.audit
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://127.0.0.1",
            timeout=10,
            event_hooks={"response": [no_server_error]},
        ) as client:
            yield client, auth


def expect(response, status, body):
    assert response.status_code == status
    assert response.json() == body
    assert response.headers["content-type"] == "application/json"


def chain_snapshot(directory):
    return {
        p.name: (p.read_bytes(), p.stat().st_size, p.stat().st_mtime_ns)
        for p in directory.glob("chain-*.jsonl")
    }


def chain_rows(directory):
    return [
        json.loads(line)
        for p in sorted(directory.glob("chain-*.jsonl"))
        for line in p.read_bytes().splitlines()
    ]


def write_rows(path, rows):
    path.write_text("".join(canonical_json(row) + "\n" for row in rows))


async def completed(service, user=OWNER):
    ident = await service.start(user, "합성 대화 요청")
    await settled(service)
    assert (await get_task(service.graph, ident))["status"] == "completed"
    service.audit.append("side_effect.draft", task_id=ident, actor_sub=user)
    return ident


async def state_snapshot(service, slack, ident):
    return copy.deepcopy(
        (
            await get_task(service.graph, ident),
            service.turns.rows,
            service.graph.checkpointer.storage,
            service.effects.outbox.rows,
            slack.sent,
        )
    )


def assert_private(response, directory, rows, *secrets_to_hide):
    encoded = response.text
    assert not re.search(r"[0-9a-f]{64}|[0-9a-f]{128}", encoded)
    for word in (
        # Fixed problem codes such as prev_hash_mismatch remain public.
        '"prev_hash"',
        "input_sha256",
        "tool_call_sha256",
        "deployment_id",
        "Authorization",
        "title",
        str(directory),
        *secrets_to_hide,
        *(row["kid"] for row in rows),
    ):
        assert word not in encoded
    assert not re.search(
        r'"(?:kid|sig|hash|dir|path|body|text)"|/(?:Users|private|tmp)/', encoded
    )


@async_test
async def test_a1_s1_to_s7_indistinguishable_404(monkeypatch, service_factory):
    service, slack, _ = service_factory(
        FakeChat(
            reply("타인 답변"),
            reply("삭제 답변"),
            draft_reply(),
            reply("정리 답변"),
            reply("첫 답변"),
            reply("둘째 답변"),
        )
    )
    async with server(monkeypatch, service) as (client, auth):
        foreign = await completed(service, OTHER)
        deleted = await completed(service)
        expect(
            await client.delete(f"/conversations/{deleted}", headers=auth(OWNER)),
            200,
            {"conversation_id": deleted, "deleted": True},
        )
        pending = await service.start(OWNER, "합성 승인 요청")
        await settled(service)
        state = await get_task(service.graph, pending)
        assert state["status"] == "awaiting_approval"
        assert service._decision(state, APPROVER) == Decision.ALLOW
        pruned = await completed(service)
        assert await service.turns.delete_conversations([pruned]) == 1
        own = await completed(service)
        second = (await service.add_turn(OWNER, own, "둘째 합성 요청"))["turn_id"]
        await settled(service)
        targets = [
            ("S1", OWNER, foreign),
            ("S2", OWNER, uuid4().hex),
            ("S3", OWNER, deleted),
            ("S4-invalid", OWNER, "not-a-valid-id"),
            ("S4-upper", OWNER, "A" * 32),
            ("S4-short", OWNER, "a" * 31),
            ("S5", APPROVER, pending),
            ("S6", OWNER, pruned),
            ("S7", OWNER, second),
        ]
        before = chain_snapshot(service.audit.directory)
        assert any(r["task_id"] == pruned for r in chain_rows(service.audit.directory))
        responses = []
        for _, user, ident in targets:
            response = await client.get(
                f"/conversations/{ident}/audit", headers=auth(user)
            )
            expect(response, 404, {"detail": "conversation_not_found"})
            responses.append((response.status_code, response.content))
            assert chain_snapshot(service.audit.directory) == before
        assert len(responses) == 9 and len(set(responses)) == 1
        assert slack.sent == [] and service.effects.outbox.rows == {}


@async_test
async def test_probe2_owner_turns_projection_and_e10(monkeypatch, service_factory):
    service, slack, _ = service_factory(
        FakeChat(
            draft_reply(),
            reply("둘째 답변"),
            reply("타인 답변"),
        )
    )
    async with server(monkeypatch, service) as (client, auth):
        ident = await service.start(OWNER, "합성 공지 요청")
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
        other = await completed(service, OTHER)
        rows = chain_rows(service.audit.directory)
        before = chain_snapshot(service.audit.directory)
        headers = auth(OWNER)
        response = await client.get(f"/conversations/{ident}/audit", headers=headers)
        assert response.status_code == 200
        value = response.json()
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
        assert any(e.get("draft_hash12") == "b" * 12 for e in events)
        assert_private(
            response,
            service.audit.directory,
            rows,
            other,
            headers["Authorization"].removeprefix("Bearer "),
        )
        summary = await client.get("/audit/verify", headers=auth(OTHER))
        assert summary.status_code == 200 and summary.json()["status"] == "pass"
        assert chain_snapshot(service.audit.directory) == before
        assert len(slack.sent) == 1


@async_test
async def test_probe3_rejects_untrusted_fields_and_invalid_values(
    monkeypatch, service_factory
):
    service, _, _ = service_factory()
    async with server(monkeypatch, service) as (client, auth):
        ident = await completed(service)
        row = chain_rows(service.audit.directory)[-1]
        hidden = secrets.token_urlsafe(32)
        bad = {**row, "text": hidden, "body": "Authorization"}
        invalid = {
            **row,
            "tool": 1,
            "decision": True,
            "check_result": 5,
            "reason": None,
            "mock_id": "a" * 64,
            "draft_hash": True,
        }
        path = next(service.audit.directory.glob("chain-*.jsonl"))
        write_rows(path, [row, bad, invalid])
        before = chain_snapshot(service.audit.directory)
        response = await client.get(
            f"/conversations/{ident}/audit", headers=auth(OWNER)
        )
        assert response.status_code == 200
        events = response.json()["turns"][0]["events"]
        assert events and all(set(e) == BASE for e in events)
        assert_private(response, service.audit.directory, [row], hidden)
        assert chain_snapshot(service.audit.directory) == before


@pytest.mark.parametrize("status", ["awaiting_approval", "running", "completed"])
@async_test
async def test_probe4_reads_never_expire_or_mutate(
    monkeypatch, service_factory, status
):
    gate, entered = asyncio.Event(), asyncio.Event()

    class HeldChat(FakeChat):
        async def complete(self, **kwargs):
            entered.set()
            await asyncio.wait_for(gate.wait(), 10)
            return await super().complete(**kwargs)

    chat = (
        HeldChat(reply("합성 답변"))
        if status == "running"
        else FakeChat(
            draft_reply() if status == "awaiting_approval" else reply("합성 답변")
        )
    )
    service, slack, now = service_factory(chat)
    async with server(monkeypatch, service) as (client, auth):
        try:
            ident = await service.start(OWNER, "합성 요청")
            if status == "running":
                await asyncio.wait_for(entered.wait(), 5)
            else:
                await settled(service)
            service.audit.append("side_effect.draft", task_id=ident, actor_sub=OWNER)
            now[0] += timedelta(minutes=31)
            state = await get_task(service.graph, ident)
            assert state["status"] == status
            if status == "awaiting_approval":
                assert now[0] > datetime.fromisoformat(state["expires_at"])
            before = await state_snapshot(service, slack, ident)
            chain = chain_snapshot(service.audit.directory)
            with monkeypatch.context() as guarded:
                guarded.setattr(
                    service, "_expire", Mock(side_effect=AssertionError("expire"))
                )
                response = await client.get(
                    f"/conversations/{ident}/audit", headers=auth(OWNER)
                )
                assert response.status_code == 200
            assert await state_snapshot(service, slack, ident) == before
            assert chain_snapshot(service.audit.directory) == chain
        finally:
            gate.set()
            await settled(service)


@async_test
async def test_e1_missing_session(monkeypatch, service_factory):
    service, _, _ = service_factory()
    async with server(monkeypatch, service) as (client, _):
        ident = await completed(service)
        for path in (f"/conversations/{ident}/audit", "/audit/verify"):
            expect(await client.get(path), 401, {"detail": "Authentication required"})


@async_test
async def test_e2_no_audit_log(monkeypatch, service_factory):
    service, _, _ = service_factory()
    ident = await completed(service)
    service.audit = None
    async with server(monkeypatch, service) as (client, auth):
        for path in (f"/conversations/{ident}/audit", "/audit/verify"):
            expect(
                await client.get(path, headers=auth(OWNER)),
                503,
                {"detail": "audit_unavailable"},
            )


@pytest.fixture
def verify_chain(service_factory, tmp_path):
    service, _, _ = service_factory()
    for _ in range(3):
        service.audit.append(
            "side_effect.draft",
            task_id=uuid4().hex,
            actor_sub=OWNER,
            tool="slack_send_message",
        )
    service.audit.anchor("synthetic", repo_root=tmp_path)
    return (
        service,
        next(service.audit.directory.glob("chain-*.jsonl")),
        chain_rows(service.audit.directory),
    )


@async_test
async def test_e3_e5_e10_global_summary(monkeypatch, verify_chain):
    service, _, rows = verify_chain
    before = chain_snapshot(service.audit.directory)
    async with server(monkeypatch, service) as (client, auth):
        response = await client.get("/audit/verify", headers=auth(OTHER))
        assert response.status_code == 200
        value = response.json()
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
        assert_private(response, service.audit.directory, rows)
        assert chain_snapshot(service.audit.directory) == before


@pytest.mark.parametrize(
    "kind,codes",
    [
        ("modify", {"hash_mismatch"}),
        ("delete", {"seq_gap"}),
        ("reorder", {"seq_order", "prev_hash_mismatch"}),
        ("tail", {"tail_truncated"}),
        ("key", {"kid_unknown"}),
    ],
)
@async_test
async def test_e4_e5_e10_tamper_detection(monkeypatch, verify_chain, kind, codes):
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
        key, previous = Ed25519PrivateKey.generate(), "0" * 64
        for row in rows:
            row["kid"], row["prev_hash"] = kid_of(key.public_key()), previous
            row["hash"] = record_hash(
                {k: v for k, v in row.items() if k not in {"hash", "sig"}}
            )
            row["sig"] = key.sign(row["hash"].encode("ascii")).hex()
            previous = row["hash"]
    write_rows(path, rows)
    before = chain_snapshot(service.audit.directory)
    async with server(monkeypatch, service) as (client, auth):
        response = await client.get("/audit/verify", headers=auth(OWNER))
        assert response.status_code == 200 and response.json()["status"] == "fail"
        problems = response.json()["chain"]["problems"]
        assert problems and codes & {p["code"] for p in problems}
        assert all(set(p) <= {"code", "seq"} for p in problems)
        assert_private(response, service.audit.directory, rows)
        assert chain_snapshot(service.audit.directory) == before


@async_test
async def test_e6_corrupt_anchor_is_fixed_503(monkeypatch, verify_chain, tmp_path):
    service, _, _ = verify_chain
    (tmp_path / "pending.jsonl").write_text("not-json\n")
    before = chain_snapshot(service.audit.directory)
    async with server(monkeypatch, service) as (client, auth):
        expect(
            await client.get("/audit/verify", headers=auth(OWNER)),
            503,
            {"detail": "audit_unavailable"},
        )
        assert chain_snapshot(service.audit.directory) == before


@async_test
async def test_e7_concurrent_requests_share_one_compute(monkeypatch, verify_chain):
    service, _, _ = verify_chain
    service._audit_verify_cache = records.VerifySummaryCache()
    # 기본 실행기를 요청 수보다 작게 고정한다: 대기가 스레드를 점유하면 굶는다.
    pool = ThreadPoolExecutor(max_workers=4)
    asyncio.get_running_loop().set_default_executor(pool)
    verify = records.verify_summary
    entered, release = threading.Event(), threading.Event()
    arrivals = []
    count = 6
    original = service.audit_verify

    async def counted():
        arrivals.append(1)
        return await original()

    def held(directory):
        entered.set()
        assert release.wait(timeout=10)
        return verify(directory)

    compute = Mock(side_effect=held)
    monkeypatch.setattr(service, "audit_verify", counted)
    monkeypatch.setattr(records, "verify_summary", compute)
    async with server(monkeypatch, service) as (client, auth):
        headers = auth(OWNER)
        requests = [
            asyncio.create_task(client.get("/audit/verify", headers=headers))
            for _ in range(count)
        ]
        try:
            async with asyncio.timeout(5):
                while len(arrivals) < count or not entered.is_set():
                    await asyncio.sleep(0.01)
            # 대기 중인 검증 요청이 공용 스레드를 점유하지 않는다.
            await asyncio.wait_for(asyncio.to_thread(lambda: None), 2)
        finally:
            release.set()
        responses = await asyncio.wait_for(asyncio.gather(*requests), 10)
        assert len(responses) == len(arrivals) == count
        assert all(r.status_code == 200 for r in responses)
        values = [r.json() for r in responses]
        assert all(v == values[0] for v in values) and values[0]["status"] == "pass"
        expect(await client.get("/audit/verify", headers=headers), 200, values[0])
        assert compute.call_count == 1
    pool.shutdown(wait=False)


@async_test
async def test_e8_unterminated_tail_uses_completed_lines(monkeypatch, service_factory):
    service, _, _ = service_factory()
    async with server(monkeypatch, service) as (client, auth):
        ident = await completed(service)
        path = next(service.audit.directory.glob("chain-*.jsonl"))
        rows = chain_rows(service.audit.directory)
        path.write_bytes(path.read_bytes() + b'{"seq":')
        before = chain_snapshot(service.audit.directory)
        response = await client.get(
            f"/conversations/{ident}/audit", headers=auth(OWNER)
        )
        assert response.status_code == 200
        assert [e["seq"] for e in response.json()["turns"][0]["events"]] == [
            r["seq"] for r in rows if r["task_id"] == ident
        ]
        summary = await client.get("/audit/verify", headers=auth(OWNER))
        assert summary.status_code in {200, 503}
        if summary.status_code == 200:
            assert summary.json()["status"] == "pass"
            assert summary.json()["chain"]["records"] == len(rows)
        else:
            expect(summary, 503, {"detail": "audit_unavailable"})
        assert chain_snapshot(service.audit.directory) == before


@async_test
async def test_e9_deleted_conversation_retains_chain(monkeypatch, service_factory):
    service, _, _ = service_factory()
    async with server(monkeypatch, service) as (client, auth):
        ident = await completed(service)
        rows = chain_rows(service.audit.directory)
        expect(
            await client.delete(f"/conversations/{ident}", headers=auth(OWNER)),
            200,
            {"conversation_id": ident, "deleted": True},
        )
        after = chain_rows(service.audit.directory)
        assert after[:-1] == rows
        assert after[-1]["event"] == "conversation.deleted"
        before = chain_snapshot(service.audit.directory)
        expect(
            await client.get(f"/conversations/{ident}/audit", headers=auth(OWNER)),
            404,
            {"detail": "conversation_not_found"},
        )
        assert chain_snapshot(service.audit.directory) == before
