"""Role-scoped request transformation and content-free verdict flags."""

import json
import re
from dataclasses import dataclass
from typing import Literal

from govops.guardrails.injection import find_injection
from govops.guardrails.pii import find_pii

QUARANTINE_MARKER = "[정책으로 일부 제외]"
INPUT_PII_KINDS = frozenset({"rrn", "mobile", "landline", "email", "card", "account"})
BLOCK_CODES = ("injection_direct", "pii_input")
TOOL_CALL_ID = re.compile(r"(?:call|toolu)_[A-Za-z0-9_-]{1,64}")
_IDENTIFIER = re.compile(
    r"(?:syn|doc)-[A-Za-z0-9_-]+|[A-Z]{2,5}-[0-9]+|"
    r"[a-fA-F0-9]{32}|[a-fA-F0-9]{64}|"
    r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}|"
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[T ][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})?)?"
)
_BOUNDARY = re.compile(r"\n|[.!?。](?=\s|$)")


@dataclass(frozen=True)
class InputVerdict:
    action: Literal["allow", "block"]
    code: str | None = None


@dataclass(frozen=True)
class ToolItem:
    index: int
    tool_call_id: str | None
    quarantined: bool
    masked: bool


@dataclass(frozen=True)
class ToolScreen:
    messages: list[dict]
    items: tuple[ToolItem, ...]
    masked: int


@dataclass(frozen=True)
class OutputFlags:
    pii_kinds: tuple[str, ...]
    injection: bool


@dataclass(frozen=True)
class RequestScreen:
    verdict: InputVerdict
    messages: list[dict]
    items: tuple[ToolItem, ...]
    masked: int


def valid_tool_call_id(value) -> bool:
    return isinstance(value, str) and TOOL_CALL_ID.fullmatch(value) is not None


def _texts(content):
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for part in content:
            if (
                isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
            ):
                yield part["text"]


def screen_user_messages(messages) -> InputVerdict:
    pii = False
    for message in messages:
        if message.get("role") != "user":
            continue
        for text in _texts(message.get("content")):
            if find_injection(text, scope="input"):
                return InputVerdict("block", "injection_direct")
            pii |= any(match.kind in INPUT_PII_KINDS for match in find_pii(text))
    return InputVerdict("block", "pii_input") if pii else InputVerdict("allow")


def _clean_text(text: str, *, structured: bool) -> tuple[str, bool, bool]:
    matches = find_injection(text, scope="tool")
    ranges = set()
    boundaries = [0, *(m.end() for m in _BOUNDARY.finditer(text)), len(text)]
    for match in matches:
        start = max(b for b in boundaries if b <= match.start)
        end = min(b for b in boundaries if b >= match.end)
        # Preserve surrounding whitespace, including line separators.
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        ranges.add((start, end))
    merged = []
    for start, end in sorted(ranges):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    for start, end in reversed(merged):
        text = text[:start] + QUARANTINE_MARKER + text[end:]
    skip = structured and (_IDENTIFIER.fullmatch(text) or valid_tool_call_id(text))
    pii = (
        []
        if skip
        else [
            m
            for m in find_pii(text)
            if m.kind in (INPUT_PII_KINDS if structured else {"rrn", "mobile", "email"})
        ]
    )
    for match in reversed(pii):
        text = text[: match.start] + f"[PII:{match.kind}]" + text[match.end :]
    return text, bool(matches), bool(pii)


def _clean_content(content: str) -> tuple[str, bool, bool]:
    try:
        value = json.loads(content)
    except (ValueError, RecursionError):
        return _clean_text(content, structured=False)
    quarantined = masked = False

    def visit(item):
        nonlocal quarantined, masked
        if isinstance(item, str):
            result, q, m = _clean_text(item, structured=True)
            quarantined |= q
            masked |= m
            return result
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, dict):
            return {key: visit(child) for key, child in item.items()}
        return item

    result = visit(value)
    return (
        json.dumps(result, ensure_ascii=False) if quarantined or masked else content,
        quarantined,
        masked,
    )


def screen_tool_messages(messages) -> ToolScreen:
    result = list(messages)
    items = []
    for index, message in enumerate(messages):
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        q = m = False
        if isinstance(content, str):
            changed, q, m = _clean_content(content)
        elif isinstance(content, list):
            changed = list(content)
            for part_index, part in enumerate(content):
                if (
                    isinstance(part, dict)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                ):
                    text, part_q, part_m = _clean_content(part["text"])
                    if part_q or part_m:
                        changed[part_index] = {**part, "text": text}
                    q |= part_q
                    m |= part_m
        else:
            changed = content
        if q or m:
            result[index] = {**message, "content": changed}
        call_id = message.get("tool_call_id")
        items.append(
            ToolItem(index, call_id if valid_tool_call_id(call_id) else None, q, m)
        )
    return ToolScreen(result, tuple(items), sum(item.masked for item in items))


def scan_output(text: str) -> OutputFlags:
    return OutputFlags(
        tuple(sorted({m.kind for m in find_pii(text) if m.kind in INPUT_PII_KINDS})),
        bool(find_injection(text, scope="input")),
    )


def screen_request(messages) -> RequestScreen:
    verdict = screen_user_messages(messages)
    if verdict.action == "block":
        return RequestScreen(verdict, messages, (), 0)
    tools = screen_tool_messages(messages)
    return RequestScreen(verdict, tools.messages, tools.items, tools.masked)
