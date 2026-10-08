"""Minimal append-only audit sink; chaining and signing are added in M4."""

import json
import os
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from govops.env import env_or
from govops.guardrails.pii import find_pii

EVENTS = frozenset(
    {
        "service.start",
        "model.call",
        "auth.failed",
        "chain.genesis",
        "chain.anchor",
        "guardrail.input_blocked",
        "guardrail.tool_quarantined",
        "guardrail.tool_masked",
        "guardrail.output_flagged",
        "guardrail.error",
        "side_effect.draft",
        "approval.requested",
        "approval.approved",
        "approval.edited",
        "approval.rejected",
        "approval.expired",
        "side_effect.attempt",
        "side_effect.result",
        "side_effect.marker_lookup",
    }
)
SYSTEM_EVENTS = frozenset(
    {"service.start", "chain.genesis", "chain.anchor", "auth.failed"}
)
AUTH_REASONS = frozenset(
    {"missing_credentials", "invalid_token", "unknown_user", "loopback_required"}
)
GUARDRAIL_PRE = frozenset({"allow", "block", "unavailable", "team_unknown", "probe"})
GUARDRAIL_POST = frozenset({"ok", "flag", "error", "none"})
TOOLS = frozenset(
    {"jira_add_comment", "jira_create_followup_ticket", "slack_send_message"}
)
CHECK_RESULTS = frozenset({"passed", "masked", "blocked"})
DECISIONS = frozenset({"approve", "edit_approve", "reject", "expired"})
REASONS = frozenset(
    {
        "recipient_not_allowed",
        "pii_blocked_by_team_policy",
        "pii_masked",
        "scope_wider_than_evidence",
        "evidence_scope_unknown",
        "ticket_not_found",
        "audit_gap",
        "already_succeeded",
        "already_failed",
        "in_progress",
        "audit_write_failed",
        "circuit_open",
        "connect",
        "rate_limited",
        "uncertain",
        "definitive",
        "unsent_confirmed",
    }
)
REASONS = REASONS | AUTH_REASONS
GUARDRAIL_CODES = frozenset(
    {
        "injection_direct",
        "pii_input",
        "guardrail_unavailable",
        "team_unknown",
        "guardrail_header_missing",
        "guardrail_stale",
        "guardrail_post_error",
    }
)
_GUARDRAIL_FORMATS = {
    "input_sha256": re.compile(r"[0-9a-f]{64}"),
    "tool_call_sha256": re.compile(r"[0-9a-f]{64}"),
    "rules_digest": re.compile(r"[0-9a-f]{12}"),
    "code_digest": re.compile(r"[0-9a-f]{12}"),
    "team": re.compile(r"[a-z][a-z-]{1,20}"),
}
_HASH = re.compile(r"[0-9a-f]{64}")
_MOCK_ID = re.compile(r"(?:message-[0-9]+|comment-[0-9]+|[A-Z]{3}-[0-9]+)")
_FIELD = re.compile(r"[a-z][a-z0-9_]*")
_FORBIDDEN = frozenset(
    {
        "text",
        "body",
        "args",
        "content",
        "description",
        "title",
        "seq",
        "ts",
        "actor",
        "event",
        "task_id",
        "hash",
        "prev_hash",
        "sig",
        "kid",
    }
)
_TASK_ID = re.compile(r"[0-9a-f]{32}")
_ACTOR_SUB = re.compile(r"demo-[a-z-]+-g[0-9]+")
_lock = threading.Lock()
_sequences: dict[Path, int] = {}
Field = str | int | bool | None


class AuditWriteError(ValueError):
    """Safe failure that must prevent execution when recording intent."""


class _InvalidAuditField(AuditWriteError, ValueError):
    pass


class AuditPort(Protocol):
    def append(
        self, event: str, *, task_id: str | None, actor_sub: str, **fields: Field
    ) -> int:
        """Persist an event or raise AuditWriteError."""
        ...


_NEW_REQUIRED = {
    "service.start": frozenset(
        {
            "policy_sha256",
            "rules_sha256",
            "access_index_sha256",
            "hook_rules_digest",
            "hook_code_digest",
        }
    ),
    "chain.genesis": frozenset(
        {"legacy_files", "legacy_lines", "legacy_last_seq", "legacy_sha256"}
    ),
    "chain.anchor": frozenset({"label"}),
    "model.call": frozenset({"team", "model", "deployment_id", "fallback", "cost_usd"}),
    "auth.failed": frozenset({"reason"}),
}
_GUARDRAIL_BUNDLE = frozenset(
    {"guardrail_pre", "guardrail_post", "guardrail_q", "guardrail_m"}
)
_NEW_OPTIONAL = {
    "model.call": _GUARDRAIL_BUNDLE,
    "auth.failed": frozenset({"suppressed"}),
}
_NEW_FIELDS = frozenset().union(*_NEW_REQUIRED.values(), *_NEW_OPTIONAL.values()) - {
    "team",
    "reason",
}
_NEW_FORMATS = {
    **{
        key: _HASH
        for key in (
            "policy_sha256",
            "rules_sha256",
            "access_index_sha256",
            "legacy_sha256",
        )
    },
    **{
        key: re.compile(r"[0-9a-f]{12}")
        for key in ("hook_rules_digest", "hook_code_digest")
    },
    "label": re.compile(r"[a-z0-9][a-z0-9-]{0,39}"),
    "model": re.compile(r"(?:agent-[a-z-]{1,30}|unknown)"),
    "deployment_id": re.compile(r"(?:[a-z0-9][a-z0-9.-]{0,63}|unknown)"),
    "cost_usd": re.compile(r"\d+\.\d{6}"),
    "team": re.compile(r"(?:[a-z][a-z-]{1,20}|none)"),
}
_NEW_ENUMS = {
    "reason": AUTH_REASONS,
    "guardrail_pre": GUARDRAIL_PRE,
    "guardrail_post": GUARDRAIL_POST,
}


def _validate_new(fields: dict[str, Field], *, event: str) -> None:
    required = _NEW_REQUIRED[event]
    allowed = required | _NEW_OPTIONAL.get(event, frozenset())
    if not required <= fields.keys() or not fields.keys() <= allowed:
        raise _InvalidAuditField("Audit event fields rejected")
    bundle = _GUARDRAIL_BUNDLE & fields.keys()
    if bundle and bundle != _GUARDRAIL_BUNDLE:
        raise _InvalidAuditField("Audit guardrail bundle rejected")
    for key, value in fields.items():
        if key in _NEW_FORMATS:
            valid = isinstance(value, str) and bool(_NEW_FORMATS[key].fullmatch(value))
        elif key in _NEW_ENUMS:
            valid = isinstance(value, str) and value in _NEW_ENUMS[key]
        elif key == "fallback":
            valid = type(value) is bool
        else:
            minimum = 1 if key == "suppressed" else 0
            valid = type(value) is int and value >= minimum
            if key in {"guardrail_q", "guardrail_m"}:
                valid = valid and value <= 10000
        if not valid:
            raise _InvalidAuditField("Audit typed field rejected")


def _validate_event(
    event: str, task_id: str | None, actor_sub: str, fields: dict[str, Field]
) -> None:
    if event not in EVENTS:
        raise _InvalidAuditField("Audit event rejected")
    if event in SYSTEM_EVENTS:
        if actor_sub != "system" or task_id is not None:
            raise _InvalidAuditField("Audit system identifier rejected")
    else:
        for identifier, pattern in ((task_id, _TASK_ID), (actor_sub, _ACTOR_SUB)):
            if not isinstance(identifier, str) or not pattern.fullmatch(identifier):
                raise _InvalidAuditField("Audit identifier rejected")
    _validate(fields, event=event)


def _validate(fields: dict[str, Field], *, event: str) -> None:
    if event in _NEW_REQUIRED:
        _validate_new(fields, event=event)
        return
    if _NEW_FIELDS & fields.keys():
        raise _InvalidAuditField("Audit event field rejected")
    if "input_sha256" in fields and not (
        event == "guardrail.input_blocked" and fields.get("code") == "injection_direct"
    ):
        raise _InvalidAuditField("Audit input hash rejected")
    for key, value in fields.items():
        if not _FIELD.fullmatch(key) or key in _FORBIDDEN:
            raise _InvalidAuditField("Audit field name rejected")
        if key == "code" or key in _GUARDRAIL_FORMATS:
            valid = isinstance(value, str) and (
                value in GUARDRAIL_CODES
                if key == "code"
                else bool(_GUARDRAIL_FORMATS[key].fullmatch(value))
            )
            if not valid:
                raise _InvalidAuditField("Audit typed field rejected")
            continue
        if value is None or isinstance(value, (int, bool)):
            continue
        if isinstance(value, str):
            if key == "tool":
                valid = value in TOOLS
            elif key == "decision":
                valid = value in DECISIONS
            elif key == "check_result":
                valid = value in CHECK_RESULTS
            elif key == "reason":
                valid = value in REASONS - AUTH_REASONS
            else:
                valid = bool(
                    _HASH.fullmatch(value) or _MOCK_ID.fullmatch(value)
                ) or value in TOOLS | DECISIONS | (REASONS - AUTH_REASONS)
            if valid:
                continue
            if find_pii(value):
                raise _InvalidAuditField("Audit PII value rejected")
        raise _InvalidAuditField("Audit field value rejected")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuditLog:
    def __init__(
        self, directory: Path | None = None, *, clock: Callable[[], datetime] = _utc_now
    ):
        self.directory = Path(
            directory
            if directory is not None
            else env_or("AUDIT_DIR", "data/interim/audit")
        ).resolve()
        self.clock = clock
        try:
            with _lock:
                self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                self.directory.chmod(0o700)
                last = 0
                for file in sorted(self.directory.glob("????-??-??.jsonl")):
                    with file.open(encoding="utf-8") as source:
                        for line in source:
                            record = json.loads(line)
                            seq = record["seq"]
                            if type(seq) is not int or seq <= last:
                                raise ValueError("Invalid sequence")
                            last = seq
                _sequences[self.directory] = last
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise AuditWriteError("Audit initialization failed") from None

    def append(
        self, event: str, *, task_id: str | None, actor_sub: str, **fields: Field
    ) -> int:
        _validate_event(event, task_id, actor_sub, fields)
        try:
            with _lock:
                timestamp = self.clock()
                seq = _sequences[self.directory] + 1
                record = {
                    "seq": seq,
                    "ts": timestamp.isoformat(),
                    "actor": {"sub": actor_sub, "act": "governed-ops-agent"},
                    "event": event,
                    "task_id": task_id,
                    **fields,
                }
                payload = (
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                file = self.directory / f"{timestamp.date().isoformat()}.jsonl"
                with file.open("a", encoding="utf-8") as target:
                    os.fchmod(target.fileno(), 0o600)
                    target.write(payload)
                    target.flush()
                    os.fsync(target.fileno())
                _sequences[self.directory] = seq
                return seq
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise AuditWriteError("Audit write failed") from None


def append_with_retry(
    sink: AuditPort, event: str, *, attempts: int = 3, **kw: Field
) -> bool:
    """Retry result records only; intent callers must use append directly."""
    if attempts < 1:
        raise ValueError("attempts must be positive")
    for _ in range(attempts):
        try:
            sink.append(event, **kw)
            return True
        except AuditWriteError:
            pass
    return False


class MemoryAuditLog:
    """Offline replay sink with the same event and field validation as JSONL."""

    def __init__(self):
        self.records: list[dict] = []

    def append(
        self, event: str, *, task_id: str | None, actor_sub: str, **fields: Field
    ) -> int:
        _validate_event(event, task_id, actor_sub, fields)
        self.records.append(
            {"event": event, "task_id": task_id, "actor_sub": actor_sub, **fields}
        )
        return len(self.records)
