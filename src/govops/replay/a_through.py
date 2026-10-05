"""Record actual A calls without changing live tool outcomes."""

import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from govops.replay.recordings import (
    Recording,
    RecordingRejected,
    _canonical,
    arguments_key,
)
from govops.replay.synthetic import SkipStats, screen_recording
from govops.tools.registry import ToolOutcome

A_TOOLS = frozenset({"search_docs", "lookup_id", "run_sql", "get_document"})


class ToolExecutor(Protocol):
    async def call(
        self, name: str, arguments: Mapping[str, Any], *, user_id: str
    ) -> ToolOutcome: ...


@dataclass(frozen=True)
class SkippedCall:
    tool: str
    arguments_hash: str


@dataclass(frozen=True)
class RunRecordSummary:
    stats: SkipStats
    skipped_calls: tuple[SkippedCall, ...]
    path: Path
    golden_eligible: bool


class RecordingToolExecutor:
    def __init__(self, inner: ToolExecutor, *, task_id: str, directory: Path):
        if not re.fullmatch(r"[\w-]+", task_id):
            raise RecordingRejected("invalid task id")
        self._inner = inner
        self._path = directory / f"{task_id}.jsonl"
        self._rows: list[str] = []
        self._stats = SkipStats()
        self._skipped: list[SkippedCall] = []
        self._rejected = False
        self._finished = False

    async def call(
        self, name: str, arguments: Mapping[str, Any], *, user_id: str
    ) -> ToolOutcome:
        if self._rejected or self._finished:
            raise RecordingRejected("recording is closed")
        if name not in A_TOOLS:
            return await self._inner.call(name, arguments, user_id=user_id)
        try:
            entry = Recording(user_id, name, dict(arguments), {}, False)
            screen_recording(entry)
            entry = Recording(**json.loads(_canonical(asdict(entry))))
            result = await self._inner.call(name, arguments, user_id=user_id)
            entry = replace(entry, result=result.payload, is_error=result.is_error)
            outcome = screen_recording(entry)
            self._stats.total += 1
            if outcome.recorded:
                self._rows.append(_canonical(asdict(entry)))
                self._stats.recorded += 1
            else:
                self._stats.skipped += 1
                self._skipped.append(SkippedCall(name, arguments_key(entry.arguments)))
        except RecordingRejected:
            self._rejected = True
            raise
        return result

    def finish(self) -> RunRecordSummary:
        if self._rejected:
            raise RecordingRejected("recording rejected")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".a-run-", dir=self._path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write("".join(row + "\n" for row in self._rows))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self._finished = True
        return RunRecordSummary(
            replace(self._stats), tuple(self._skipped), self._path, not self._skipped
        )
