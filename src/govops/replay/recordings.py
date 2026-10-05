"""Deterministic, credential-free synthetic A response recordings."""

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_SECRET_KEYS = {"authorization", "token", "bearer", "password", "api_key"}
_BEARER = re.compile(r"Bearer ", re.IGNORECASE)


@dataclass(frozen=True)
class Recording:
    caller: str
    tool: str
    arguments: dict
    result: dict
    is_error: bool


class RecordingRejected(ValueError):
    """A recording violates the replay format or safety rules."""


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def arguments_key(arguments: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(dict(arguments)).encode("utf-8")).hexdigest()


def validate_recording(entry: Recording) -> None:
    def reject(kind: str) -> None:
        raise RecordingRejected(f"{kind}: caller={entry.caller} tool={entry.tool}")

    if (
        not isinstance(entry.caller, str)
        or not entry.caller
        or not isinstance(entry.tool, str)
        or not entry.tool
        or not isinstance(entry.arguments, dict)
        or not isinstance(entry.result, dict)
        or type(entry.is_error) is not bool
    ):
        raise RecordingRejected("invalid recording fields")

    def walk(
        value: Any, *, check_docs: bool = False, result_root: bool = False
    ) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    reject("invalid JSON key")
                if key.lower() in _SECRET_KEYS:
                    reject("credential key")
                if _BEARER.search(key):
                    reject("credential value")
                if (
                    check_docs
                    and key == "doc_id"
                    and (not isinstance(child, str) or not child.startswith("syn-"))
                ):
                    reject("non-synthetic doc_id")
                sql_rows = result_root and entry.tool == "run_sql" and key == "rows"
                walk(child, check_docs=check_docs and not sql_rows)
        elif isinstance(value, list):
            for child in value:
                walk(child, check_docs=check_docs)
        elif isinstance(value, str) and _BEARER.search(value):
            reject("credential value")

    walk(entry.caller)
    walk(entry.tool)
    walk(entry.arguments)
    # Complete credential checks before a non-synthetic document can stop validation.
    walk(entry.result)
    walk(entry.result, check_docs=True, result_root=True)
    invalid = False
    try:
        _canonical(asdict(entry))
    except (TypeError, ValueError, RecursionError):
        invalid = True
    if invalid:
        reject("invalid JSON value")


def _key(entry: Recording) -> tuple[str, str, str]:
    return entry.caller, entry.tool, arguments_key(entry.arguments)


class RecordingSet:
    def __init__(self, entries: Iterable[Recording] = ()) -> None:
        self._records: dict[tuple[str, str, str], Recording] = {}
        for entry in entries:
            validate_recording(entry)
            key = _key(entry)
            if key in self._records:
                raise RecordingRejected(
                    f"duplicate recording: caller={entry.caller} tool={entry.tool}"
                )
            self._records[key] = entry

    @classmethod
    def load(cls, directory: Path) -> "RecordingSet":
        path = directory / "recordings.jsonl"
        if not path.exists():
            return cls()
        parsed = None
        try:
            payloads = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            parsed = [Recording(**payload) for payload in payloads]
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            pass
        if parsed is None:
            raise RecordingRejected("invalid recording file")
        return cls(parsed)

    def lookup(
        self, caller: str, tool: str, arguments: Mapping[str, Any]
    ) -> Recording | None:
        return self._records.get((caller, tool, arguments_key(arguments)))

    def entries(self) -> list[Recording]:
        return [self._records[key] for key in sorted(self._records)]


def write_recordings(directory: Path, entries: Iterable[Recording]) -> None:
    records = RecordingSet(entries).entries()
    content = "".join(_canonical(asdict(entry)) + "\n" for entry in records)
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".recordings-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / "recordings.jsonl")
    finally:
        Path(temporary).unlink(missing_ok=True)
