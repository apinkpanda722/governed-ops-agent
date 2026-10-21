"""Private rollout state and append-only records; no service dependencies."""

import fcntl
import json
import math
import os
import re
import tempfile
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path

from govops.env import env_or
from govops.rollout.policy import CLASSIFY_STATUSES, CLASSIFY_TAIL, OFF, RolloutState

_STATE_KEYS = {"pct_bp", "kill", "rolled_back_at", "reason"}
_EXPOSURE_KEYS = {
    "assigned": {
        "event",
        "ts",
        "conversation_id",
        "user_sha256",
        "bucket",
        "variant",
        "pct_bp",
        "kill",
    },
    "variant_missing": {"event", "ts", "conversation_id"},
    "rollback": {"event", "ts", "reason", "pct_bp"},
}
_CLASSIFY_KEYS = {"ts", "turn_id", "status", "started_ms", "ended_ms"}


def rollout_dir() -> Path:
    return Path(env_or("GOVOPS_ROLLOUT_DIR", "data/interim/rollout"))


def _valid_pct(value: object) -> bool:
    return type(value) is int and 0 <= value <= 10000


def _valid_state(row: object) -> bool:
    return (
        isinstance(row, dict)
        and set(row) == _STATE_KEYS
        and _valid_pct(row["pct_bp"])
        and type(row["kill"]) is bool
        and (row["rolled_back_at"] is None or isinstance(row["rolled_back_at"], str))
        and (row["reason"] is None or isinstance(row["reason"], str))
    )


def read_state(directory: Path) -> RolloutState:
    try:
        row = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return OFF
    except (OSError, ValueError):
        return RolloutState(0, True, None, "state_invalid")
    if not _valid_state(row):
        return RolloutState(0, True, None, "state_invalid")
    return RolloutState(**row)


def _ensure_directory(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)


def write_state(directory: Path, state: RolloutState) -> None:
    _ensure_directory(directory)
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(asdict(state), stream, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(Path(temporary), directory / "state.json")
    finally:
        Path(temporary).unlink(missing_ok=True)


def _hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is not None
    )


def _validate_exposure(row: Mapping[str, object]) -> None:
    event = row.get("event")
    if not isinstance(event, str) or set(row) != _EXPOSURE_KEYS.get(event):
        raise ValueError("invalid_exposure_keys")
    if not isinstance(row["ts"], str):
        raise ValueError("invalid_exposure_format")  # noqa: TRY004 - record contract
    if "conversation_id" in row and not _hex(row["conversation_id"], 32):
        raise ValueError("invalid_conversation_id")
    if event == "assigned" and not (
        _hex(row["user_sha256"], 64)
        and type(row["bucket"]) is int
        and 0 <= row["bucket"] < 10000
        and row["variant"] in ("v1", "v2")
        and _valid_pct(row["pct_bp"])
        and type(row["kill"]) is bool
    ):
        raise ValueError("invalid_assignment")
    if event == "rollback" and not (
        _valid_pct(row["pct_bp"])
        and isinstance(row["reason"], str)
        and re.fullmatch(r"[a-z][a-z_]{0,39}", row["reason"])
    ):
        raise ValueError("invalid_rollback")


def _validate_classify(row: Mapping[str, object]) -> None:
    if set(row) != _CLASSIFY_KEYS:
        raise ValueError("invalid_classify_keys")
    if not (
        isinstance(row["ts"], str)
        and _hex(row["turn_id"], 32)
        and isinstance(row["status"], str)
        and row["status"] in CLASSIFY_STATUSES
        and type(row["started_ms"]) in (int, float)
        and type(row["ended_ms"]) in (int, float)
        and math.isfinite(row["started_ms"])
        and math.isfinite(row["ended_ms"])
        and row["ended_ms"] >= row["started_ms"]
    ):
        raise ValueError("invalid_classify_format")


def _append(directory: Path, name: str, row: Mapping[str, object]) -> None:
    content = (
        json.dumps(dict(row), ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")
    _ensure_directory(directory)
    fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.fchmod(fd, 0o600)
        remaining = memoryview(content)
        while remaining:
            written = os.write(fd, remaining)
            if written == 0:
                raise OSError("record_write_failed")
            remaining = remaining[written:]
    finally:
        os.close(fd)


def append_exposure(directory: Path, row: Mapping[str, object]) -> None:
    _validate_exposure(row)
    _append(directory, "exposures.jsonl", row)


def append_classify(directory: Path, row: Mapping[str, object]) -> None:
    _validate_classify(row)
    _append(directory, "classify.jsonl", row)


def _read_rows(
    path: Path,
    validate: Callable[[Mapping[str, object]], None],
    limit: int | None = None,
) -> list[dict]:
    rows = deque(maxlen=limit)
    try:
        stream = path.open(encoding="utf-8")
    except FileNotFoundError:
        return []
    try:
        with stream:
            for line in stream:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("invalid_record")  # noqa: TRY004 - record contract
                validate(row)
                rows.append(row)
    except UnicodeError:
        raise ValueError("invalid_record") from None
    return list(rows)


def read_exposures(directory: Path) -> list[dict]:
    return _read_rows(directory / "exposures.jsonl", _validate_exposure)


def read_classify_tail(directory: Path, limit: int = CLASSIFY_TAIL) -> list[dict]:
    if type(limit) is not int or limit < 0:
        raise ValueError("invalid_tail_limit")
    return _read_rows(directory / "classify.jsonl", _validate_classify, limit)
