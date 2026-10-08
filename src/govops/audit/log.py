"""Append-only signed audit chain with bounded directory locking."""

import fcntl
import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from govops.audit import crypto
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
_directory_locks: dict[Path, threading.Lock] = {}
Field = str | int | bool | None


class AuditWriteError(ValueError):
    """Safe failure that must prevent execution when recording intent."""

    def __init__(self, message: str = "", code: str = "audit_write_failed"):
        self.code = code
        super().__init__(message)


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
        self,
        directory: Path | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
        lock_timeout: float = 10.0,
    ):
        self.directory = Path(
            directory
            if directory is not None
            else env_or("AUDIT_DIR", "data/interim/audit")
        ).resolve()
        self.clock = clock
        self.lock_timeout = lock_timeout
        self._key = crypto.load_signing_key()
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.directory.chmod(0o700)
            with _lock:
                self._thread_lock = _directory_locks.setdefault(
                    self.directory, threading.Lock()
                )
        except OSError:
            raise AuditWriteError("Audit initialization failed") from None

    @contextmanager
    def _locked(self):
        deadline = time.monotonic() + self.lock_timeout
        if not self._thread_lock.acquire(timeout=max(0.0, self.lock_timeout)):
            raise AuditWriteError("Audit lock timed out", code="audit_lock_timeout")
        fd = None
        acquired = False
        try:
            fd = os.open(self.directory, os.O_RDONLY)
            delay = 0.005
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise AuditWriteError(
                            "Audit lock timed out", code="audit_lock_timeout"
                        ) from None
                    time.sleep(min(delay, remaining))
                    delay = min(delay * 2, 0.05)
            yield
        finally:
            try:
                if fd is not None:
                    try:
                        if acquired:
                            fcntl.flock(fd, fcntl.LOCK_UN)
                    finally:
                        os.close(fd)
            finally:
                self._thread_lock.release()

    def _last_record(self) -> dict | None:
        for path in sorted(self.directory.glob("chain-*.jsonl"), reverse=True):
            with path.open("rb") as source:
                size = source.seek(0, os.SEEK_END)
                if not size:
                    continue
                source.seek(-1, os.SEEK_END)
                if source.read(1) != b"\n":
                    raise AuditWriteError(
                        "Audit chain corrupt", code="audit_chain_corrupt"
                    )
                # Scan backwards in bounded blocks, excluding the terminal newline.
                end = size - 1
                chunks = []
                while end:
                    start = max(0, end - 4096)
                    source.seek(start)
                    chunk = source.read(end - start)
                    index = chunk.rfind(b"\n")
                    if index >= 0:
                        chunks.append(chunk[index + 1 :])
                        break
                    chunks.append(chunk)
                    end = start
                try:
                    record = json.loads(b"".join(reversed(chunks)))
                    if type(record["seq"]) is not int or record["seq"] < 1:
                        raise ValueError
                    if not isinstance(record["hash"], str) or not _HASH.fullmatch(
                        record["hash"]
                    ):
                        raise ValueError
                    return record
                except (ValueError, TypeError, KeyError, UnicodeError):
                    raise AuditWriteError(
                        "Audit chain corrupt", code="audit_chain_corrupt"
                    ) from None
        return None

    def _legacy_fields(self) -> dict:
        files = sorted(self.directory.glob("????-??-??.jsonl"))
        hashes = []
        lines = last = 0
        for path in files:
            digest = hashlib.sha256()
            try:
                with path.open("rb") as source:
                    for line in source:
                        digest.update(line)
                        record = json.loads(line)
                        seq = record["seq"]
                        if type(seq) is not int:
                            raise ValueError
                        last = seq
                        lines += 1
            except (ValueError, TypeError, KeyError, UnicodeError):
                raise AuditWriteError(
                    "Audit legacy invalid", code="audit_legacy_invalid"
                ) from None
            hashes.append([path.name, digest.hexdigest()])
        return {
            "legacy_files": len(files),
            "legacy_lines": lines,
            "legacy_last_seq": last,
            "legacy_sha256": hashlib.sha256(
                crypto.canonical_json(hashes).encode("utf-8")
            ).hexdigest(),
        }

    def _write(
        self,
        timestamp: datetime,
        seq: int,
        previous: str,
        event: str,
        task_id: str | None,
        actor_sub: str,
        fields: dict,
    ) -> dict:
        record = {
            "seq": seq,
            "ts": timestamp.isoformat(),
            "prev_hash": previous,
            "actor": {"sub": actor_sub, "act": "governed-ops-agent"},
            "event": event,
            "task_id": task_id,
            **fields,
            "kid": self._key.kid,
        }
        record["hash"] = crypto.record_hash(record)
        record["sig"] = self._key.sign(record["hash"])
        payload = crypto.canonical_json(record) + "\n"
        path = self.directory / f"chain-{timestamp.date().isoformat()}.jsonl"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with path.open("a", encoding="utf-8") as target:
                target.write(payload)
                target.flush()
                os.fsync(target.fileno())
        finally:
            os.close(fd)
        return record

    def anchor(
        self,
        label: str,
        *,
        repo_root: Path | None = None,
        pending_file: Path | None = None,
    ) -> dict:
        from govops.audit.anchor import AuditAnchorError, validate_label, write_pending

        validate_label(label)
        root = (
            Path(__file__).resolve().parents[3]
            if repo_root is None
            else Path(repo_root)
        )
        try:
            relative = self.directory.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            raise AuditAnchorError("audit_anchor_dir_outside_repo") from None
        record = self._append_record(
            "chain.anchor", task_id=None, actor_sub="system", label=label
        )
        row = {
            "dir": relative,
            **{key: record[key] for key in ("seq", "hash", "sig", "kid", "ts")},
            "label": label,
        }
        write_pending(row, pending_file)
        return row

    def append(
        self, event: str, *, task_id: str | None, actor_sub: str, **fields: Field
    ) -> int:
        return self._append_record(
            event, task_id=task_id, actor_sub=actor_sub, **fields
        )["seq"]

    def _append_record(
        self, event: str, *, task_id: str | None, actor_sub: str, **fields: Field
    ) -> dict:
        _validate_event(event, task_id, actor_sub, fields)
        try:
            with self._locked():
                timestamp = self.clock()
                if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                    raise AuditWriteError("Audit timestamp must be aware")
                timestamp = timestamp.astimezone(UTC)
                last = self._last_record()
                if last is None:
                    legacy = self._legacy_fields()
                    last = self._write(
                        timestamp,
                        legacy["legacy_last_seq"] + 1,
                        "0" * 64,
                        "chain.genesis",
                        None,
                        "system",
                        legacy,
                    )
                record = self._write(
                    timestamp,
                    last["seq"] + 1,
                    last["hash"],
                    event,
                    task_id,
                    actor_sub,
                    fields,
                )
                return record
        except AuditWriteError:
            raise
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
