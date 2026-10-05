"""ASGI fault matrix using virtual time and actual mock state."""

import asyncio
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_outbox import sync

from govops.audit.log import AuditLog, AuditWriteError
from govops.eval.duplicates import count_effects
from govops.eval.spec import Effect
from govops.mocks.auth import MockUser, TokenHashStore
from govops.mocks.faults import FaultState
from govops.mocks.jira import JiraState
from govops.mocks.service import create_rest_app
from govops.mocks.slack import CHANNELS, SlackState
from govops.obs.trace import SpanKind, trace
from govops.tools.effects import EffectRequest, execute_effect, reconcile_effect
from govops.tools.jira import JiraClient
from govops.tools.markers import idempotency_key
from govops.tools.outbox import InMemoryOutboxStore, OutboxRow, OutboxState
from govops.tools.registry import CreateFollowup
from govops.tools.resilience import ToolPolicy
from govops.tools.slack import SlackClient

TOOLS = ["slack_send_message", "jira_add_comment", "jira_create_followup_ticket"]


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def __call__(self):
        return self.now

    def timestamp(self):
        return datetime(2026, 10, 5, tzinfo=UTC) + timedelta(seconds=self.now)

    async def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class Transport(httpx.AsyncBaseTransport):
    def __init__(self, app, mode):
        self.inner = httpx.ASGITransport(app=app)
        self.mode = mode
        self.posts = self.gets = 0

    async def handle_async_request(self, request):
        if request.method == "POST":
            self.posts += 1
            if self.posts == 1 and self.mode == "connect":
                raise httpx.ConnectError("synthetic", request=request)
        else:
            self.gets += 1
        response = await self.inner.handle_async_request(request)
        if request.method == "POST" and self.posts == 1:
            if self.mode in ("loss", "short", "known_limit"):
                raise httpx.ReadError("synthetic", request=request)
            if self.mode == "broken":
                return httpx.Response(200, content=b"{broken", request=request)
        return response


class Audit:
    def __init__(self, log):
        self.log, self.fail, self.events = log, None, []

    def append(self, event, **fields):
        self.events.append((event, fields))
        if self.fail == event or (
            self.fail == "second"
            and event == "side_effect.attempt"
            and fields["send_no"] == 2
        ):
            raise AuditWriteError("synthetic")
        return self.log.append(event, **fields)


@pytest.fixture(params=TOOLS)
def setup(request, monkeypatch, tmp_path):
    tool = request.param
    clock = Clock()

    async def wait(seconds):
        await clock.sleep(seconds)
        if seconds >= 12:
            raise TimeoutError()

    faults = FaultState(wait=wait)
    tickets = json.loads(Path("data/seed/tickets.json").read_text())["tickets"]
    ticket = next(t for t in tickets if t["access_level"] == "public")
    js, ss = (
        JiraState([ticket], clock=clock.timestamp),
        SlackState(clock=clock.timestamp),
    )
    service = "slack" if tool.startswith("slack") else "jira"
    token = secrets.token_urlsafe(32)
    monkeypatch.setattr("govops.broker.credentials.credential_for", lambda *_: token)
    auth = TokenHashStore(
        (
            (
                MockUser("demo-platform-g3", "platform", 3),
                hashlib.sha256(token.encode()).hexdigest(),
            ),
        )
    )
    app = create_rest_app(
        service, state=ss if service == "slack" else js, token_store=auth, faults=faults
    )
    args = (
        {"channel": "announce", "text": "합성 메시지"}
        if service == "slack"
        else {"ticket_id": ticket["ticket_id"], "body": "합성 코멘트"}
        if tool == "jira_add_comment"
        else {"parent_ticket_id": ticket["ticket_id"], "title": "합성 후속 티켓"}
    )
    endpoint = dict(
        zip(
            TOOLS,
            ["slack.send", "jira.comment.create", "jira.child.create"],
            strict=True,
        )
    )[tool]
    kind = dict(
        zip(
            TOOLS,
            ["slack_message", "jira_comment", "jira_followup_ticket"],
            strict=True,
        )
    )[tool]
    return {
        "clock": clock,
        "faults": faults,
        "app": app,
        "req": EffectRequest(
            "syn-task", "demo-platform-g3", tool, args, "syn-approval"
        ),
        "endpoint": endpoint,
        "js": js,
        "ss": ss,
        "service": service,
        "target": "announce" if service == "slack" else ticket["ticket_id"],
        "kind": kind,
        "audit": Audit(AuditLog(tmp_path)),
        "outbox": InMemoryOutboxStore(),
    }


def rule(s, action, nth=None):
    return {
        "endpoint": s["endpoint"],
        "nth": [1] if nth is None else nth,
        "action": action,
    }


async def run(s, mode="normal", req=None, policy=None, reconcile=None):
    transport = Transport(s["app"], mode)
    async with httpx.AsyncClient(transport=transport) as http:
        kw = {
            "jira": JiraClient("http://synthetic", http_client=http),
            "slack": SlackClient("http://synthetic", http_client=http),
            "outbox": s["outbox"],
            "audit": s["audit"],
            "policy": policy
            or ToolPolicy(
                clock=s["clock"], sleep=s["clock"].sleep, jitter=lambda: 1, hard=10
            ),
            "sleep": s["clock"].sleep,
        }
        records = []
        sink = SimpleNamespace(emit=records.append, close_trace=lambda _: None)
        with trace("a" * 32, sinks=[sink]):
            result = (
                await reconcile_effect(reconcile, user_id="demo-platform-g3", **kw)
                if reconcile
                else await execute_effect(req or s["req"], **kw)
            )
        s["records"] = records
    return result, transport.posts, transport.gets


def counts(s):
    return count_effects(
        {"slack": s["ss"].report(bodies=True), "jira": s["js"].report(bodies=True)},
        task_id="syn-task",
        expected=[Effect(kind=s["kind"], target=s["target"], count=1, structure={})],
    )


@sync
@pytest.mark.parametrize(
    "mode,posts,lookups,state,duplicates,missing",
    [
        ("soft", 1, 0, "succeeded", 0, 0),
        ("hard", 1, 1, "succeeded", 0, 0),
        ("before", 2, 2, "succeeded", 0, 0),
        ("after", 1, 1, "succeeded", 0, 0),
        ("all", 3, 6, "uncertain", 0, 1),
        ("rate", 2, 0, "succeeded", 0, 0),
        ("connect", 2, 0, "succeeded", 0, 0),
        ("loss", 1, 1, "succeeded", 0, 0),
        ("short", 1, 2, "succeeded", 0, 0),
        ("known_limit", 2, 2, "succeeded", 1, 0),
        ("broken", 1, 1, "succeeded", 0, 0),
    ],
)
async def test_fault_matrix_known_limit(
    setup, mode, posts, lookups, state, duplicates, missing
):
    s = setup
    actions = {
        "soft": {"type": "delay", "when": "after_apply", "ms": 4000},
        "hard": {"type": "delay", "when": "after_apply", "ms": 12000},
        "before": {"type": "status", "when": "before", "status": 500},
        "after": {"type": "status", "when": "after_apply", "status": 500},
        "all": {"type": "status", "when": "before", "status": 500},
        "rate": {"type": "rate_limit", "seconds": 1},
        "short": {"type": "hide_for", "ms": 1000},
        "known_limit": {"type": "hide_for", "ms": 6000},
    }
    if mode in actions:
        s["faults"].set([rule(s, actions[mode], "all" if mode == "all" else [1])])
    result, p, g = await run(s, mode)
    c = counts(s)
    assert (c.duplicates, c.missing, result.state, p, g, result.lookups) == (
        duplicates,
        missing,
        state,
        posts,
        lookups,
        lookups,
    )
    row = next(iter(s["outbox"].rows.values()))
    assert row.state == result.state
    assert row.attempts == posts
    assert sum(r.kind == SpanKind.IDEMPOTENCY_LOOKUP for r in s["records"]) == lookups
    if mode == "rate":
        assert sum(s["clock"].waits) >= 1
    if mode == "soft":
        assert any(
            r.attributes.get("soft_timeout_exceeded") is True
            for r in s["records"]
            if r.kind == SpanKind.TOOL_CALL
        )


@sync
@pytest.mark.parametrize(
    "fail,state,posts",
    [
        ("side_effect.attempt", "failed", 0),
        ("second", "uncertain", 1),
        ("side_effect.result", "succeeded", 1),
    ],
)
async def test_audit_failure(setup, fail, state, posts):
    s = setup
    s["audit"].fail = fail
    if fail == "second":
        s["faults"].set([rule(s, {"type": "status", "when": "before", "status": 500})])
    result, p, _ = await run(s)
    assert (result.state, p) == (state, posts)
    assert result.audit_gap == (fail == "side_effect.result")
    if fail == "side_effect.result":
        row = next(iter(s["outbox"].rows.values()))
        assert row.audit_gap is True
    assert counts(s).missing == (posts == 0 or fail == "second")
    if fail != "side_effect.result":
        assert result.reason == "audit_write_failed"
        assert s["audit"].events[-1][0] == "side_effect.result"


@sync
@pytest.mark.parametrize(
    "state,reason",
    [
        ("succeeded", "already_succeeded"),
        ("failed", "already_failed"),
        ("attempted", "in_progress"),
        ("recorded", "in_progress"),
        ("uncertain", "in_progress"),
    ],
)
@pytest.mark.parametrize("audit_failure", [False, True])
async def test_claim_reexecution(setup, state, reason, audit_failure, monkeypatch):
    s = setup
    args = s["req"].args
    if s["req"].tool == "jira_create_followup_ticket":
        args = CreateFollowup.model_validate(args).model_dump()
    key = idempotency_key("syn-task", s["req"].tool, args)
    await s["outbox"].claim(
        OutboxRow(
            key,
            "syn-task",
            s["req"].tool,
            s["target"],
            "a" * 64,
            "syn-approval",
            OutboxState(state),
        )
    )
    original = await s["outbox"].get(key)
    updates = []
    update = s["outbox"].update

    async def tracked_update(key, **changes):
        updates.append(changes)
        return await update(key, **changes)

    monkeypatch.setattr(s["outbox"], "update", tracked_update)
    if audit_failure:
        s["audit"].fail = "side_effect.result"
    result, p, g = await run(s)
    assert (p, g, result.reason) == (0, 0, reason)
    assert result.audit_gap is audit_failure
    assert await s["outbox"].get(key) == original
    assert not updates
    event, fields = s["audit"].events[-1]
    assert (
        event == "side_effect.result"
        and fields["claimed"] is False
        and fields["reason"] == reason
    )


@sync
async def test_concurrent(setup):
    results = await asyncio.gather(run(setup), run(setup))
    assert sum(r[1] for r in results) == 1
    assert counts(setup).duplicates == counts(setup).missing == 0


@sync
@pytest.mark.parametrize("present", [False, True])
async def test_reconcile_without_send(setup, present):
    s = setup
    if not present:
        s["faults"].set(
            [rule(s, {"type": "status", "when": "before", "status": 500}, "all")]
        )
    await run(s)
    row = next(iter(s["outbox"].rows.values()))
    await s["outbox"].update(row.idempotency_key, state=OutboxState.UNCERTAIN)
    s["faults"].reset()
    result, p, g = await run(s, reconcile=row.idempotency_key)
    assert (p, g, result.state) == (
        0,
        1 if present else 2,
        "succeeded" if present else "failed",
    )


@sync
async def test_circuit_no_requests_after_open(setup):
    s = setup
    lookup = (
        "slack.history"
        if s["service"] == "slack"
        else "jira.comment.list"
        if s["req"].tool == "jira_add_comment"
        else "jira.child.list"
    )
    action = {"type": "status", "when": "before", "status": 500}
    s["faults"].set(
        [rule(s, action, "all"), {"endpoint": lookup, "nth": "all", "action": action}]
    )
    policy = ToolPolicy(clock=s["clock"], jitter=lambda: 0)
    for i in range(3):
        await run(
            s,
            req=EffectRequest(
                f"syn-task-{i}",
                "demo-platform-g3",
                s["req"].tool,
                s["req"].args,
                "syn-approval",
            ),
            policy=policy,
        )
    before = s["faults"].stats()
    result, p, g = await run(
        s,
        req=EffectRequest(
            "syn-final",
            "demo-platform-g3",
            s["req"].tool,
            s["req"].args,
            "syn-approval",
        ),
        policy=policy,
    )
    assert (result.state, result.reason, p, g) == ("failed", "circuit_open", 0, 0)
    assert s["faults"].stats() == before


def test_targets_match_seed():
    channels = {c["channel_id"] for c in CHANNELS}
    tickets = {
        t["ticket_id"]
        for t in json.loads(Path("data/seed/tickets.json").read_text())["tickets"]
    }
    for task in json.loads(Path("data/eval/tasks.json").read_text()):
        for effect in task["expected"]["effects"]:
            assert effect["target"] in (
                channels if effect["kind"] == "slack_message" else tickets
            )


@sync
async def test_lookup_failure_stops_resend_and_reconcile_preserves_state(setup):
    s = setup
    lookup = (
        "slack.history"
        if s["service"] == "slack"
        else "jira.comment.list"
        if s["req"].tool == "jira_add_comment"
        else "jira.child.list"
    )
    action = {"type": "status", "when": "before", "status": 500}
    s["faults"].set(
        [rule(s, action, "all"), {"endpoint": lookup, "nth": "all", "action": action}]
    )
    result, posts, gets = await run(s)
    assert (result.state, posts, gets) == ("uncertain", 1, 1)
    assert counts(s).duplicates == 0 and counts(s).missing == 1
    row = next(iter(s["outbox"].rows.values()))
    result, posts, gets = await run(s, reconcile=row.idempotency_key)
    assert (result.state, posts, gets) == ("uncertain", 0, 1)


@sync
async def test_lookup_audit_gap_does_not_resend(setup):
    s = setup
    s["audit"].fail = "side_effect.marker_lookup"
    result, posts, gets = await run(s, "loss")
    assert (result.state, result.audit_gap, posts, gets) == ("succeeded", True, 1, 1)
    assert counts(s).duplicates == counts(s).missing == 0


@sync
async def test_invalid_input_no_claim_or_call(setup):
    s = setup
    req = EffectRequest(
        "syn-task",
        "demo-platform-g3",
        s["req"].tool,
        {**s["req"].args, "user_id": "injected"},
        "syn-approval",
    )
    result, posts, gets = await run(s, req=req)
    assert (result.state, result.reason, posts, gets) == (
        "failed",
        "invalid_argument",
        0,
        0,
    )
    assert not s["outbox"].rows and not s["audit"].events


@sync
async def test_definitive_rejection_no_marker_lookup(setup):
    s = setup
    args = {**s["req"].args}
    if s["service"] == "slack":
        args["channel"] = "team-finance"
    elif s["req"].tool == "jira_add_comment":
        args["ticket_id"] = "ZZZ-9999"
    else:
        args["parent_ticket_id"] = "ZZZ-9999"
    result, posts, gets = await run(
        s,
        req=EffectRequest(
            "syn-task", "demo-platform-g3", s["req"].tool, args, "syn-approval"
        ),
    )
    assert (result.state, posts, gets) == ("failed", 1, 0)


@sync
async def test_intent_precedes_claim_and_post(setup):
    s = setup
    store = s["outbox"]

    class OrderedStore:
        async def claim(self, row):
            assert s["audit"].events[-1][0] == "side_effect.attempt"
            return await store.claim(row)

        async def update(self, key, **changes):
            return await store.update(key, **changes)

    s["outbox"] = OrderedStore()
    result, posts, gets = await run(s)
    assert (result.state, posts, gets) == ("succeeded", 1, 0)
