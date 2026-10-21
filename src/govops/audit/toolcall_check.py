"""Read-only tool-call/trace reconciliation; print only counts and fixed codes."""

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from govops.audit.log import TOOL_CALL_STATUSES
from govops.audit.records import _chain_files, _complete
from govops.env import env_or

FAILURE_CODES = frozenset(
    {"no_turns", "trace_missing", "count_mismatch", "audit_unavailable"}
)
_COUNTED_STATUSES = frozenset(
    {"ok", "tool_error", "tool_not_available", "invalid_argument", "tool_unavailable"}
)


@dataclass(frozen=True)
class CheckResult:
    turns: int = 0
    tool_calls: int = 0
    spans: int = 0
    code: str | None = None

    @property
    def ok(self) -> bool:
        return self.code is None

    @property
    def line(self) -> str:
        if self.code is not None:
            return f"FAIL audit_toolcall_check {self.code}"
        return (
            f"PASS audit_toolcall_check turns={self.turns} "
            f"tool_calls={self.tool_calls} spans={self.spans}"
        )


def _audit_counts(directory: Path, since: datetime):
    counts: Counter[str] = Counter()
    turns: set[str] = set()
    unavailable = False
    try:
        files = _chain_files(directory)
    except (OSError, ValueError):
        return counts, turns, True
    for path in files:
        try:
            data = _complete(path.read_bytes())
        except OSError:
            unavailable = True
            continue
        for line in data.split(b"\n")[:-1]:
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise TypeError
                if row.get("event") != "tool.call":
                    continue
                task_id, status = row["task_id"], row["status"]
                timestamp = datetime.fromisoformat(row["ts"])
                if (
                    not isinstance(task_id, str)
                    or re.fullmatch(r"[0-9a-f]{32}", task_id) is None
                    or status not in TOOL_CALL_STATUSES
                    or timestamp.utcoffset() is None
                ):
                    raise ValueError
                if timestamp >= since:
                    turns.add(task_id)
                if status in _COUNTED_STATUSES:
                    counts[task_id] += 1
            except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
                unavailable = True
    return counts, turns, unavailable


def _span_count(path: Path) -> int:
    count = 0
    for line in _complete(path.read_bytes()).split(b"\n")[:-1]:
        row = json.loads(line)
        if not isinstance(row, dict):
            raise TypeError
        if row.get("kind") == "tool_call" and row.get("name") != "tool_attempt":
            count += 1
    return count


def check(audit_dir: Path, trace_dir: Path, *, since: datetime) -> CheckResult:
    if since.utcoffset() is None:
        return CheckResult(code="audit_unavailable")
    counts, turns, unavailable = _audit_counts(audit_dir, since)
    if not turns:
        # Unreadable evidence cannot establish that the target set is empty.
        return CheckResult(code="audit_unavailable" if unavailable else "no_turns")
    spans = 0
    missing = mismatch = False
    for task_id in sorted(turns):
        try:
            count = _span_count(trace_dir / f"{task_id}.jsonl")
        except OSError:
            missing = True
            continue
        except (ValueError, TypeError, UnicodeError, RecursionError):
            mismatch = True
            continue
        spans += count
        mismatch |= counts[task_id] != count
    code = (
        "trace_missing"
        if missing
        else "count_mismatch"
        if mismatch
        else "audit_unavailable"
        if unavailable
        else None
    )
    return CheckResult(
        len(turns), sum(counts[task_id] for task_id in turns), spans, code
    )


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's usual diagnostics can echo paths or arbitrary input.
        raise ValueError


def _utc_timestamp(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value)
    if timestamp.utcoffset() != timedelta(0):
        raise ValueError
    return timestamp


def main(argv: list[str] | None = None) -> int:
    parser = _SafeParser(description=__doc__)
    parser.add_argument(
        "--since",
        type=_utc_timestamp,
        default=datetime.now(UTC) - timedelta(minutes=60),
    )
    parser.add_argument(
        "--audit-dir", type=Path, default=env_or("AUDIT_DIR", "data/interim/audit")
    )
    default_trace = Path(__file__).resolve().parents[3] / "logs" / "traces"
    parser.add_argument(
        "--trace-dir",
        type=Path,
        default=env_or("GOVOPS_TRACE_DIR", str(default_trace)),
    )
    try:
        args = parser.parse_args(argv)
        result = check(args.audit_dir, args.trace_dir, since=args.since)
    except (ValueError, OSError):
        result = CheckResult(code="audit_unavailable")
    print(result.line)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
