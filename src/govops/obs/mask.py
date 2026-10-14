"""Transmission policy: input.request/output.answer/output.draft/tool.args/tool.result.

Unknown string attributes use the conservative tool-result policy. Nested mappings
and sequences follow the same key convention. Tool results retain only ids/excerpt.
Registered secrets are stored as hashes, never plaintext or printable values.
"""

import hashlib
import json
import re
import threading
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from govops.guardrails.pii import find_pii, mask_pii

Role = Literal["request", "answer", "draft", "tool_result", "tool_args"]
TOOL_EXCERPT_MAX_CHARS = 300
_ROLES = {
    "input.request": "request",
    "output.answer": "answer",
    "output.draft": "draft",
    "tool.result": "tool_result",
    "tool.args": "tool_args",
}
_SENSITIVE = re.compile(
    r"authorization|token|secret|api_key|password|cookie", re.IGNORECASE
)
_SECRET_PATTERNS = [
    re.compile(r"\bAuthorization\s*[\"\']?\s*[:=]\s*[^\r\n,}]+", re.IGNORECASE),
    re.compile(r"\bBearer\s+[^\s\"\',;}]+", re.IGNORECASE),
    re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+){2}"),
]
# Exact keys and complete formats only; secret scrubbing remains mandatory.
DEPLOYMENT_IDS = frozenset(
    {
        "openai-gpt-6-luna",
        "openai-gpt-6-luna-only",
        "openai-gpt-6-luna-down",
        "anthropic-claude-sonnet-5-5",
        "anthropic-claude-haiku-5-5",
    }
)
IDENTIFIER_FORMATS = {
    "generation_id": re.compile(r"^[0-9a-f]{16}$"),
    "trace_id": re.compile(r"^[0-9a-f]{32}$"),
    "task_id": re.compile(r"^[0-9a-f]{32}$"),
    "prompt_hash": re.compile(r"^[0-9a-f]{64}$"),
    "content_hash": re.compile(r"^[0-9a-f]{64}$"),
    "deployment_id": DEPLOYMENT_IDS,
}
_registry: dict[int, set[bytes]] = {}
_lock = threading.Lock()


def register_secret_values(values: Iterable[str]) -> None:
    with _lock:
        for value in values:
            if isinstance(value, str) and len(value) >= 8:
                _registry.setdefault(len(value), set()).add(
                    hashlib.sha256(value.encode()).digest()
                )


def _secret_intervals(text: str) -> list[tuple[int, int]]:
    with _lock:
        registry = {length: hashes.copy() for length, hashes in _registry.items()}
    intervals = []
    for length, hashes in registry.items():
        for start in range(len(text) - length + 1):
            if hashlib.sha256(text[start : start + length].encode()).digest() in hashes:
                intervals.append((start, start + length))
    for pattern in _SECRET_PATTERNS:
        intervals.extend(
            (match.start(), match.end()) for match in pattern.finditer(text)
        )
    return intervals


def scrub_secrets(text: str) -> str:
    intervals = _secret_intervals(text)
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    for start, end in reversed(merged):
        text = text[:start] + "[REDACTED]" + text[end:]
    return text


def _clean(text: str) -> str:
    return mask_pii(scrub_secrets(text))


def _tool_result(value: Any) -> dict[str, Any]:
    ids = []
    if isinstance(value, Mapping):
        raw_ids = value.get("ids", [])
        if isinstance(raw_ids, (list, tuple)):
            ids = [_clean(item) for item in raw_ids if isinstance(item, str)]
        text = value.get("excerpt", value.get("body", value.get("text", "")))
        if not isinstance(text, str):
            text = ""
    else:
        text = value if isinstance(value, str) else ""
    # Detect against the full source before cutting so a boundary cannot expose
    # half a credential or PII value. Never pull later source text into the excerpt.
    cutoff = min(len(text), TOOL_EXCERPT_MAX_CHARS)
    intervals = _secret_intervals(text) + [(m.start, m.end) for m in find_pii(text)]
    clipped = False
    for start, end in sorted(intervals, reverse=True):
        if start < cutoff < end:
            cutoff = start
            clipped = True
    excerpt = _clean(text[:cutoff])
    if clipped:
        excerpt += "[REDACTED]"
    excerpt = excerpt[:TOOL_EXCERPT_MAX_CHARS]
    if len(text) > TOOL_EXCERPT_MAX_CHARS:
        excerpt += "…(잘림)"
    return {"ids": ids, "excerpt": excerpt}


def mask_text(text: str, *, role: Role) -> str:
    if role in ("request", "answer", "draft"):
        cleaned = _clean(text)
        return (
            cleaned[:8000] + f"…(잘림, 원문 {len(text)}자)"
            if len(cleaned) > 8000
            else cleaned
        )
    if role == "tool_args":
        try:
            value = json.loads(text)
        except ValueError:
            return _clean(text)
        if isinstance(value, (dict, list)):
            cleaned_value = _walk(value, "tool_args")
            if cleaned_value == value:
                return text
            return json.dumps(cleaned_value, ensure_ascii=False)
        return _clean(text)
    try:
        value = json.loads(text)
    except ValueError:
        value = text
    return json.dumps(_tool_result(value), ensure_ascii=False)


def _walk(value: Any, role: str | None = None) -> Any:
    if role == "tool_result":
        if isinstance(value, str):
            return json.loads(mask_text(value, role="tool_result"))
        return _tool_result(value)
    if isinstance(value, Mapping):
        result = {}
        for key, child in value.items():
            safe_key = _clean(str(key))
            identifier_format = IDENTIFIER_FORMATS.get(str(key))
            if _SENSITIVE.search(str(key)):
                result[safe_key] = "[REDACTED]"
            elif (
                isinstance(child, str)
                and identifier_format is not None
                and (
                    child in identifier_format
                    if isinstance(identifier_format, frozenset)
                    else identifier_format.fullmatch(child) is not None
                )
            ):
                result[safe_key] = scrub_secrets(child)
            else:
                result[safe_key] = _walk(child, _ROLES.get(str(key), role))
        return result
    if isinstance(value, (list, tuple)):
        return [_walk(child, role) for child in value]
    if isinstance(value, str):
        if role is None:
            return _tool_result(value)["excerpt"]
        return mask_text(value, role=role)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[REDACTED]"


def mask_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    return _walk(attributes)
