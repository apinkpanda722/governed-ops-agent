"""Approved effect execution, marker recovery, and send-free reconciliation."""

import asyncio
from dataclasses import dataclass, replace

from pydantic import ValidationError

from govops.audit.log import AuditPort, AuditWriteError, append_with_retry
from govops.obs.trace import SpanKind, span
from govops.tools.jira import comment_id, followup_id
from govops.tools.markers import has_marker, idempotency_key, make_marker
from govops.tools.outbox import OPEN, OutboxRow, OutboxState
from govops.tools.registry import AddComment, CreateFollowup, SendMessage
from govops.tools.resilience import FailureKind, ToolPolicy, classify
from govops.tools.slack import message_id

LOOKUP_WAIT = 2.0
MAX_SENDS = 3
_MODELS = {
    "slack_send_message": SendMessage,
    "jira_add_comment": AddComment,
    "jira_create_followup_ticket": CreateFollowup,
}


@dataclass(frozen=True)
class EffectRequest:
    task_id: str
    user_id: str
    tool: str
    args: dict
    approval_id: str


@dataclass(frozen=True)
class EffectResult:
    state: OutboxState
    mock_id: str | None
    sends: int
    lookups: int
    audit_gap: bool
    reason: str | None


def _fields(row, user_id):
    return {
        "task_id": row.task_id,
        "actor_sub": user_id,
        "tool": row.tool,
        "idempotency_key": row.idempotency_key,
        "args_sha256": row.args_sha256,
    }


def _result(row, sends=0, lookups=0, reason=None):
    return EffectResult(
        row.state,
        row.mock_id,
        sends,
        lookups,
        row.audit_gap,
        row.reason if reason is None else reason,
    )


async def _finish(
    row, *, outbox, audit, user_id, sends, lookups, claimed=True, reason=None
):
    code = row.reason if reason is None else reason
    ok = append_with_retry(
        audit,
        "side_effect.result",
        **_fields(row, user_id),
        claimed=claimed,
        reason=code,
        mock_id=row.mock_id,
        sends=sends,
        lookups=lookups,
    )
    if not ok and claimed:
        row = await outbox.update(row.idempotency_key, audit_gap=True)
    result = _result(row, sends, lookups, code)
    return replace(result, audit_gap=True) if not ok else result


async def _lookup(row, *, user_id, jira, slack, policy, audit, outbox, sleep):
    service = "slack" if row.tool == "slack_send_message" else "jira"
    lookup = {
        "slack_send_message": slack.channel_history,
        "jira_add_comment": jira.ticket_comments,
        "jira_create_followup_ticket": jira.ticket_children,
    }[row.tool]
    for n in (1, 2):
        found = None
        failed = False
        try:
            with span(SpanKind.IDEMPOTENCY_LOOKUP, "marker_lookup", service=service):
                items = await policy.call_once(
                    service, lambda: lookup(user_id, row.target)
                )
                if has_marker(items, row.idempotency_key):
                    item = next(
                        i for i in items if has_marker([i], row.idempotency_key)
                    )
                    found = (
                        item.get("message_id")
                        or item.get("comment_id")
                        or item.get("ticket_id")
                    )
                    if not isinstance(found, str):
                        raise ValueError("invalid marker response")
        except Exception:  # noqa: BLE001 — safe failure without transport details
            failed = True
        ok = append_with_retry(
            audit,
            "side_effect.marker_lookup",
            **_fields(row, user_id),
            lookup_no=n,
            found=found is not None,
            failed=failed,
            mock_id=found,
        )
        if not ok:
            await outbox.update(row.idempotency_key, audit_gap=True)
        if failed:
            return None, n, False
        if found is not None:
            return found, n, True
        if n == 1:
            await sleep(LOOKUP_WAIT)
    return None, 2, True


async def execute_effect(
    req: EffectRequest,
    *,
    jira,
    slack,
    outbox,
    audit: AuditPort,
    policy: ToolPolicy,
    sleep=asyncio.sleep,
) -> EffectResult:
    try:
        if req.tool not in _MODELS or not req.approval_id:
            raise ValueError("invalid request")
        args = _MODELS[req.tool].model_validate(req.args).model_dump()
        key = idempotency_key(req.task_id, req.tool, args)
        args_hash = idempotency_key("", "", args)
    except (ValidationError, ValueError, TypeError):
        return EffectResult(OutboxState.FAILED, None, 0, 0, False, "invalid_argument")
    target = args.get("channel") or args.get("ticket_id") or args["parent_ticket_id"]
    row = OutboxRow(
        key,
        req.task_id,
        req.tool,
        target,
        args_hash,
        req.approval_id,
        OutboxState.RECORDED,
    )
    # Intent must exist before even claiming the ledger key.
    try:
        audit.append("side_effect.attempt", **_fields(row, req.user_id), send_no=1)
    except AuditWriteError:
        recorded = append_with_retry(
            audit,
            "side_effect.result",
            **_fields(row, req.user_id),
            claimed=False,
            reason="audit_write_failed",
            sends=0,
            lookups=0,
        )
        return EffectResult(
            OutboxState.FAILED, None, 0, 0, not recorded, "audit_write_failed"
        )
    row, claimed = await outbox.claim(row)
    if not claimed:
        reason = (
            "already_succeeded"
            if row.state == OutboxState.SUCCEEDED
            else "already_failed"
            if row.state == OutboxState.FAILED
            else "in_progress"
        )
        return await _finish(
            row,
            outbox=outbox,
            audit=audit,
            user_id=req.user_id,
            sends=0,
            lookups=0,
            claimed=False,
            reason=reason,
        )
    marker = make_marker(req.task_id, key, req.approval_id)
    service = "slack" if req.tool == "slack_send_message" else "jira"
    send, extract = {
        "slack_send_message": (slack.send_message, message_id),
        "jira_add_comment": (jira.add_comment, comment_id),
        "jira_create_followup_ticket": (jira.create_followup, followup_id),
    }[req.tool]
    sends = lookups = 0
    uncertain = False
    state, reason, mock_id = OutboxState.FAILED, None, None
    for send_no in range(1, MAX_SENDS + 1):
        if send_no > 1:
            try:
                audit.append(
                    "side_effect.attempt", **_fields(row, req.user_id), send_no=send_no
                )
            except AuditWriteError:
                state = OutboxState.UNCERTAIN if sends else OutboxState.FAILED
                reason = "audit_write_failed"
                break
        row = await outbox.update(key, state=OutboxState.ATTEMPTED)

        async def post():
            nonlocal sends
            sends += 1
            await outbox.update(key, attempts=sends)
            return extract(await send(req.user_id, **args, metadata=marker))

        try:
            mock_id = await policy.call_once(service, post)
            state = OutboxState.SUCCEEDED
            break
        except Exception as exc:  # noqa: BLE001 — classify unknown failures as uncertain
            kind = classify(exc)
            reason = kind.value
            if kind in (FailureKind.DEFINITIVE, FailureKind.CIRCUIT_OPEN):
                state = OutboxState.UNCERTAIN if uncertain else OutboxState.FAILED
                break
            if kind == FailureKind.UNCERTAIN:
                uncertain = True
                row = await outbox.update(key, state=OutboxState.UNCERTAIN)
                mock_id, count, available = await _lookup(
                    row,
                    user_id=req.user_id,
                    jira=jira,
                    slack=slack,
                    policy=policy,
                    audit=audit,
                    outbox=outbox,
                    sleep=sleep,
                )
                lookups += count
                if mock_id is not None:
                    state, reason = OutboxState.SUCCEEDED, None
                    break
                state = OutboxState.UNCERTAIN
                if not available or send_no == MAX_SENDS:
                    break
            elif send_no == MAX_SENDS:
                state = OutboxState.UNCERTAIN if uncertain else OutboxState.FAILED
                break
            retry_after = (
                getattr(exc, "retry_after", None)
                if kind == FailureKind.RATE_LIMITED
                else None
            )
            await sleep(
                policy.cap_retry_after(retry_after)
                if retry_after is not None
                else policy.backoff(send_no)
            )
    if state == OutboxState.SUCCEEDED:
        reason = None
    row = await outbox.update(key, state=state, reason=reason, mock_id=mock_id)
    return await _finish(
        row,
        outbox=outbox,
        audit=audit,
        user_id=req.user_id,
        sends=sends,
        lookups=lookups,
    )


async def reconcile_effect(
    key,
    *,
    user_id,
    jira,
    slack,
    outbox,
    audit: AuditPort,
    policy: ToolPolicy,
    sleep=asyncio.sleep,
) -> EffectResult:
    row = await outbox.get(key)
    if row is None:
        return EffectResult(OutboxState.FAILED, None, 0, 0, False, "not_found")
    count = 0
    if row.state in OPEN:
        mock_id, count, available = await _lookup(
            row,
            user_id=user_id,
            jira=jira,
            slack=slack,
            policy=policy,
            audit=audit,
            outbox=outbox,
            sleep=sleep,
        )
        if available:
            row = await outbox.update(
                key,
                state=OutboxState.SUCCEEDED if mock_id else OutboxState.FAILED,
                mock_id=mock_id,
                reason=None if mock_id else "unsent_confirmed",
            )
        else:
            row = await outbox.get(key)
    return await _finish(
        row, outbox=outbox, audit=audit, user_id=user_id, sends=0, lookups=count
    )
