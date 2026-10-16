"""Read-only display projections and summaries of the existing audit verifier."""

import json
import threading
import time
from collections.abc import Callable, Collection, Hashable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from govops.audit import log
from govops.audit.crypto import AuditKeyError, load_public_keys
from govops.audit.verify import (
    LEGACY,
    ROOT,
    read_anchor_lines,
    verify_anchor_dirs,
    verify_chain,
)
from govops.env import env_or

ALLOWED_EVENT_FIELDS: frozenset[str] = frozenset(
    {
        "tool",
        "decision",
        "check_result",
        "reason",
        "code",
        "mock_id",
        "fallback",
        "cost_usd",
    }
)
_ENVELOPE = frozenset(
    {"seq", "ts", "event", "actor", "task_id", "hash", "prev_hash", "sig", "kid"}
)
_ENUMS = {
    "tool": log.TOOLS,
    "decision": log.DECISIONS,
    "check_result": log.CHECK_RESULTS,
    "reason": log.REASONS,
    "code": log.GUARDRAIL_CODES,
}


class AuditUnavailable(Exception):
    """A fixed public error, with no evidence or filesystem details."""

    code = "audit_unavailable"

    def __init__(self):
        super().__init__(self.code)


def _chain_files(directory: Path) -> list[Path]:
    # iterdir propagates unreadable/missing directories; glob can hide those errors.
    return sorted(p for p in directory.iterdir() if p.match("chain-*.jsonl"))


def _complete(data: bytes) -> bytes:
    return data[: data.rfind(b"\n") + 1]


def _display_field(key: str, value) -> bool:
    if key in _ENUMS:
        return isinstance(value, str) and value in _ENUMS[key]
    if key == "fallback":
        return type(value) is bool
    pattern = log._MOCK_ID if key == "mock_id" else log._NEW_FORMATS["cost_usd"]
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _event(row: dict) -> dict:
    actor = row["actor"]
    if (
        not isinstance(actor, dict)
        or set(actor) != {"sub", "act"}
        or actor["act"] != "governed-ops-agent"
    ):
        raise ValueError
    log._validate_event(
        row["event"],
        row["task_id"],
        actor["sub"],
        {key: value for key, value in row.items() if key not in _ENVELOPE},
    )
    if type(row["seq"]) is not int or row["seq"] < 1:
        raise ValueError
    if (
        not isinstance(row["ts"], str)
        or datetime.fromisoformat(row["ts"]).utcoffset() is None
    ):
        raise ValueError
    if not isinstance(row["hash"], str) or log._HASH.fullmatch(row["hash"]) is None:
        raise ValueError
    event = {
        "seq": row["seq"],
        "ts": row["ts"],
        "event": row["event"],
        "actor": actor["sub"],
        "hash12": row["hash"][:12],
    }
    for key in ALLOWED_EVENT_FIELDS & row.keys():
        if _display_field(key, row[key]):
            event[key] = row[key]
    draft_hash = row.get("draft_hash")
    if isinstance(draft_hash, str) and log._HASH.fullmatch(draft_hash):
        event["draft_hash12"] = draft_hash[:12]
    return event


def read_events(
    directory: Path, task_ids: Collection[str], *, limit: int = 500
) -> tuple[dict[str, list[dict]], bool]:
    """Return only requested turns, in file/line order, up to a shared limit."""
    if limit < 0:
        raise ValueError("limit must be nonnegative")
    events: dict[str, list[dict]] = {task: [] for task in task_ids}
    count = 0
    try:
        for path in _chain_files(Path(directory)):
            for line in _complete(path.read_bytes()).split(b"\n")[:-1]:
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict) or row.get("task_id") not in events:
                        continue
                    event = _event(row)
                except (ValueError, TypeError, KeyError, UnicodeError):
                    continue
                if count == limit:
                    return events, True
                events[row["task_id"]].append(event)
                count += 1
    except OSError:
        raise AuditUnavailable() from None
    return events, False


def _snapshot_report(directory, files, last_data, *, root, keys, anchors):
    """Keep relative anchor identity while verifying only completed appends."""
    relative = directory.relative_to(root)
    with TemporaryDirectory(prefix="govops-audit-records-") as temporary:
        snapshot_root = Path(temporary)
        target = snapshot_root / relative
        target.mkdir(parents=True, exist_ok=True)
        complete_bytes = 0
        for path in files:
            data = _complete(last_data) if path == files[-1] else path.read_bytes()
            complete_bytes += len(data)
            (target / path.name).write_bytes(data)
        if not complete_bytes:
            # Genesis itself is still being appended; there is no snapshot yet.
            raise AuditUnavailable()
        for path in directory.glob(LEGACY):
            (target / path.name).write_bytes(path.read_bytes())
        return verify_chain(
            target, public_keys=keys, anchors=anchors, repo_root=snapshot_root
        )


def verify_summary(
    directory: Path,
    *,
    public_key_path: Path | None = None,
    anchor_paths: Sequence[Path] | None = None,
    repo_root: Path | None = None,
) -> dict:
    """Project existing verification results to counts and fixed codes only."""
    root = Path(ROOT if repo_root is None else repo_root).resolve()

    def rooted(path):
        return (root / path).resolve()

    directory = rooted(directory)
    public = rooted(
        public_key_path
        if public_key_path is not None
        else Path(env_or("AUDIT_PUBLIC_KEY_FILE", "data/seed/audit_public_key.pem"))
    )
    committed = rooted(Path("docs/results/audit-anchors.jsonl"))
    paths = [
        rooted(path)
        for path in (
            anchor_paths
            if anchor_paths is not None
            else [
                Path(
                    env_or(
                        "AUDIT_ANCHORS_PENDING_FILE",
                        "data/interim/audit/anchors-pending.jsonl",
                    )
                ),
                committed,
            ]
        )
    ]
    try:
        keys = load_public_keys(public)
        anchors = read_anchor_lines(paths)
        counts = {"committed": 0, "pending": 0}
        for path in paths:
            if path.exists():
                counts["committed" if path == committed else "pending"] += len(
                    path.read_bytes().splitlines()
                )
        files = _chain_files(directory)
        last_data = files[-1].read_bytes() if files else b""
        if last_data and not last_data.endswith(b"\n"):
            report = _snapshot_report(
                directory, files, last_data, root=root, keys=keys, anchors=anchors
            )
        else:
            report = verify_chain(
                directory, public_keys=keys, anchors=anchors, repo_root=root
            )
        # Check original directories, not the temporary snapshot's partial tree.
        missing = verify_anchor_dirs(anchors, repo_root=root, verified=[])
    except (AuditKeyError, ValueError, OSError):
        raise AuditUnavailable() from None
    problems = [
        {
            "code": problem.code,
            **({"seq": problem.seq} if problem.seq is not None else {}),
        }
        for problem in (*report.problems, *missing)
    ]
    return {
        "status": "fail" if problems else "pass",
        "checked_at": datetime.now(UTC).isoformat(),
        "chain": {
            "records": report.records,
            "files": len(files),
            "anchors_checked": report.anchors_checked,
            "problems": problems,
        },
        "anchors": counts,
        "legacy_unverified": {
            "files": report.legacy_files,
            "lines": report.legacy_lines,
        },
        "known_limit": "tail_after_last_anchor_not_detected",
    }


class VerifySummaryCache:
    """Per-instance TTL cache with computation serialized under one lock."""

    def __init__(
        self, ttl: float = 10.0, *, clock: Callable[[], float] = time.monotonic
    ):
        self.ttl = ttl
        self.clock = clock
        self._lock = threading.Lock()
        self._values: dict[Hashable, tuple[float, dict]] = {}

    def get(self, key, compute: Callable[[], dict]) -> dict:
        with self._lock:
            cached = self._values.get(key)
            if cached is not None and self.clock() < cached[0]:
                return cached[1]
            value = compute()
            self._values[key] = (self.clock() + self.ttl, value)
            return value
