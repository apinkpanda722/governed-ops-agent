"""Collect exact tool-message evidence from an injected read-only saver."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from govops.grounding.probe import RowLink


@dataclass(frozen=True)
class EvidenceItem:
    tool_name: str | None
    content: str
    chars: int


@dataclass(frozen=True)
class Evidence:
    status: Literal["ok", "evidence_missing", "evidence_oversize"]
    items: tuple[EvidenceItem, ...]
    total_chars: int
    truncated_items: int


def _field(value: object, name: str, default=None):
    return (
        value.get(name, default)
        if isinstance(value, dict)
        else getattr(value, name, default)
    )


def _collect(checkpoint: object) -> tuple[EvidenceItem, ...]:
    if not isinstance(checkpoint, dict):
        return ()
    values = checkpoint.get("channel_values", {})
    messages = values.get("messages", []) if isinstance(values, dict) else []
    if not isinstance(messages, (list, tuple)):
        return ()
    items: list[EvidenceItem] = []
    tool_names: dict[str, str] = {}
    for message in messages:
        role = (
            _field(message, "role")
            if isinstance(message, dict)
            else _field(message, "type")
        )
        if role in {"assistant", "ai"}:
            tool_names = {}
            calls = _field(message, "tool_calls", [])
            if isinstance(calls, (list, tuple)):
                for call in calls:
                    call_id = _field(call, "id")
                    function = _field(call, "function")
                    name = (
                        _field(function, "name")
                        if function is not None
                        else _field(call, "name")
                    )
                    if isinstance(call_id, str) and isinstance(name, str):
                        tool_names[call_id] = name
        elif role == "tool":
            content = _field(message, "content")
            if isinstance(content, str) and content.strip():
                call_id = _field(message, "tool_call_id")
                name = tool_names.get(call_id) if isinstance(call_id, str) else None
                items.append(EvidenceItem(name, content, len(content)))
    return tuple(items)


async def load_evidence(saver, link: RowLink, *, max_chars: int) -> Evidence:
    """Use the first candidate with tools, without listing, merging, or truncating."""
    if saver is not None:
        for thread_id in link.thread_ids:
            try:
                saved = await asyncio.wait_for(
                    saver.aget_tuple({"configurable": {"thread_id": thread_id}}), 30
                )
            except Exception:  # noqa: BLE001, S112 — never log private backend messages
                continue
            items = _collect(saved.checkpoint) if saved is not None else ()
            if not items:
                continue
            total_chars = sum(item.chars for item in items)
            truncated = sum(item.content.endswith("…(잘림)") for item in items)
            if total_chars > max_chars:
                return Evidence("evidence_oversize", (), total_chars, truncated)
            return Evidence("ok", items, total_chars, truncated)
    return Evidence("evidence_missing", (), 0, 0)
