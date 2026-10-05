"""Sequential recorded A responses, with optional mock read delegation."""

import json
from collections import defaultdict, deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from govops.replay.a_through import A_TOOLS, ToolExecutor
from govops.replay.llm import RerecordRequired
from govops.replay.recordings import (
    Recording,
    _canonical,
    arguments_key,
    validate_recording,
)
from govops.tools.registry import ToolNotExecutable, ToolOutcome, _ids


class ReplayToolExecutor:
    def __init__(self, path: Path, *, inner: ToolExecutor | None = None):
        self._inner = inner
        self._calls: dict[tuple[str, str, str], deque[Recording]] = defaultdict(deque)
        try:
            entries = [
                Recording(**json.loads(line))
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            for entry in entries:
                validate_recording(entry)
                if entry.tool not in A_TOOLS:
                    raise RerecordRequired()
                self._calls[
                    (entry.caller, entry.tool, arguments_key(entry.arguments))
                ].append(entry)
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            raise RerecordRequired() from None

    @classmethod
    def load(
        cls, path: Path, *, inner: ToolExecutor | None = None
    ) -> "ReplayToolExecutor":
        return cls(path, inner=inner)

    async def call(
        self, name: str, arguments: Mapping[str, Any], *, user_id: str
    ) -> ToolOutcome:
        if name not in A_TOOLS:
            if self._inner is not None and name in {
                "jira_get_ticket",
                "jira_search_tickets",
                "slack_get_history",
                "slack_list_channels",
            }:
                return await self._inner.call(name, arguments, user_id=user_id)
            raise ToolNotExecutable("Tool is not executable")
        calls = self._calls.get((user_id, name, arguments_key(arguments)))
        if not calls:
            raise RerecordRequired()
        entry = calls.popleft()
        payload = json.loads(_canonical(entry.result))
        return ToolOutcome(payload, entry.is_error, _ids(payload, {"doc_id"}))
