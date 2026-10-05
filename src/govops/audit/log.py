"""Minimal append-only audit sink; chaining and signing are added in M4."""

import json
import os
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from govops.guardrails.pii import find_pii

EVENTS = frozenset(
    {
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
    }
)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_lock = threading.Lock()
_sequences: dict[Path, int] = {}
Field = str | int | bool | None


class AuditWriteError(Exception):
    """Safe failure that must prevent execution when recording intent."""


class _InvalidAuditField(AuditWriteError, ValueError):
    pass


class AuditPort(Protocol):
    def append(
        self, event: str, *, task_id: str, actor_sub: str, **fields: Field
    ) -> int:
        """Persist an event or raise AuditWriteError."""
        ...


def _validate(fields: dict[str, Field]) -> None:
    for key, value in fields.items():
        if not _FIELD.fullmatch(key) or key in _FORBIDDEN:
            raise _InvalidAuditField("Audit field name rejected")
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
                valid = value in REASONS
            else:
                valid = (
                    bool(_HASH.fullmatch(value) or _MOCK_ID.fullmatch(value))
                    or value in TOOLS | DECISIONS | REASONS
                )
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
            else os.environ.get("AUDIT_DIR", "data/interim/audit")
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
        self, event: str, *, task_id: str, actor_sub: str, **fields: Field
    ) -> int:
        if event not in EVENTS:
            raise _InvalidAuditField("Audit event rejected")
        _validate(fields)
        for identifier in (task_id, actor_sub):
            if (
                not isinstance(identifier, str)
                or not _IDENTIFIER.fullmatch(identifier)
                or find_pii(identifier)
            ):
                raise _InvalidAuditField("Audit identifier rejected")
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
