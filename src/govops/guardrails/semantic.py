"""Pure selection, request construction and monotone semantic quarantine."""

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from govops.guardrails.pii import mask_pii
from govops.guardrails.screen import (
    QUARANTINE_MARKER,
    screen_tool_messages,
    screen_user_messages,
    valid_tool_call_id,
)

_CONFIG_FILE = Path(__file__).with_name("classifier_v2.json")
_INTEGER_FIELDS = frozenset(
    {
        "max_completion_tokens",
        "concurrency",
        "round_item_limit",
        "min_item_chars",
        "item_max_chars",
        "head_chars",
        "tail_chars",
    }
)
_TIME_FIELDS = frozenset({"timeout_s", "connect_timeout_s"})
_TEXT_FIELDS = frozenset({"model", "truncation_marker", "tool_notice"})
_CONFIG_KEYS = (
    _INTEGER_FIELDS
    | _TIME_FIELDS
    | _TEXT_FIELDS
    | {"version", "labels", "system_prompts"}
)
_IDENTIFIER = re.compile(
    r"syn-[A-Za-z0-9_-]+|[A-Z]{2,5}-[0-9]+|"
    r"[a-fA-F0-9]{32}|[a-fA-F0-9]{64}|"
    r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}|"
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[T ][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})?)?"
)
_WHITESPACE = re.compile(r"\s*")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate config key")
        result[key] = value
    return result


def load_config(path: str | Path | None = None) -> dict:
    """Read only classifier configuration and reject unknown or invalid fields."""
    try:
        config = json.loads(
            Path(path if path is not None else _CONFIG_FILE).read_bytes(),
            object_pairs_hook=_unique_object,
        )
        if not isinstance(config, dict) or set(config) != _CONFIG_KEYS:
            raise ValueError
        if type(config["version"]) is not int or config["version"] != 1:
            raise ValueError
        for field in _INTEGER_FIELDS:
            value = config[field]
            minimum = 0 if field in {"head_chars", "tail_chars"} else 1
            if type(value) is not int or value < minimum:
                raise ValueError
        for field in _TIME_FIELDS:
            value = config[field]
            if type(value) is not float or not math.isfinite(value) or value <= 0:
                raise ValueError
        for field in _TEXT_FIELDS:
            if not isinstance(config[field], str) or not config[field].strip():
                raise ValueError
        for field, keys in (
            ("labels", {"safe", "unsafe"}),
            ("system_prompts", {"user_input", "tool_result"}),
        ):
            values = config[field]
            if not isinstance(values, dict) or set(values) != keys:
                raise ValueError
            if any(
                not isinstance(value, str) or not value.strip()
                for value in values.values()
            ):
                raise ValueError
        labels = config["labels"]
        if labels["safe"] == labels["unsafe"] or any(
            value != value.strip() for value in labels.values()
        ):
            raise ValueError
        if config["model"] != "guardrail-v2":
            raise ValueError
        if config["head_chars"] + config["tail_chars"] > config["item_max_chars"]:
            raise ValueError
        return config
    except (ValueError, TypeError, OSError, RecursionError):
        raise ValueError("Invalid classifier config") from None


def config_sha256(path: str | Path | None = None) -> str:
    return hashlib.sha256(
        Path(path if path is not None else _CONFIG_FILE).read_bytes()
    ).hexdigest()


@dataclass(frozen=True)
class Item:
    kind: Literal["user", "tool"]
    text: str
    message_index: int | None
    path: tuple
    truncated: bool


@dataclass(frozen=True)
class ClassifierRequest:
    messages: list[dict]
    tools: list[dict] | None
    tool_choice: str | None


def _transmission(text: str, config: Mapping) -> tuple[str, bool]:
    text = mask_pii(text)
    if len(text) <= config["item_max_chars"]:
        return text, False
    tail = text[-config["tail_chars"] :] if config["tail_chars"] else ""
    return text[: config["head_chars"]] + config["truncation_marker"] + tail, True


def select_user_item(request: str, config: Mapping) -> Item | None:
    if screen_user_messages([{"role": "user", "content": request}]).action == "block":
        return None
    text, truncated = _transmission(request, config)
    return Item("user", text, None, (), truncated)


def _marker_only(text: str) -> bool:
    return not text.replace(QUARANTINE_MARKER, "").strip()


def _json_strings(content: str) -> dict[tuple, tuple[str, int, int]] | None:
    """Return value paths and lexical spans, preserving unrelated JSON bytes."""
    try:
        json.loads(content)
        decoder = json.JSONDecoder()
        strings = {}

        def visit(start: int, path: tuple) -> int:
            start = _WHITESPACE.match(content, start).end()
            char = content[start]
            if char not in "[{":
                value, end = decoder.raw_decode(content, start)
                if isinstance(value, str):
                    strings[path] = (value, start, end)
                return end
            is_object = char == "{"
            closing = "}" if is_object else "]"
            cursor = _WHITESPACE.match(content, start + 1).end()
            index = 0
            while content[cursor] != closing:
                if is_object:
                    key, cursor = decoder.raw_decode(content, cursor)
                    cursor = _WHITESPACE.match(content, cursor).end() + 1
                else:
                    key = index
                cursor = visit(cursor, (*path, key))
                cursor = _WHITESPACE.match(content, cursor).end()
                if content[cursor] == ",":
                    cursor = _WHITESPACE.match(content, cursor + 1).end()
                index += 1
            return cursor + 1

        visit(0, ())
        return strings
    except (ValueError, RecursionError):
        return None


def select_tool_items(
    messages: Sequence[Mapping], config: Mapping
) -> tuple[list[Item], int]:
    screened = screen_tool_messages(messages)
    items = []
    excess = 0
    for index in reversed(range(len(screened.messages))):
        message = screened.messages[index]
        if message.get("role") != "tool" or not isinstance(message.get("content"), str):
            continue
        content = message["content"]
        values = _json_strings(content)
        candidates = (
            [((), content)]
            if values is None
            else [
                (path, text)
                for path, (text, _, _) in values.items()
                if len(text) >= config["min_item_chars"]
                and not _IDENTIFIER.fullmatch(text)
            ]
        )
        for path, text in candidates:
            if _marker_only(text):
                continue
            if len(items) >= config["round_item_limit"]:
                excess += 1
                continue
            text, truncated = _transmission(text, config)
            items.append(Item("tool", text, index, path, truncated))
    return items, excess


def build_request(item: Item, config: Mapping) -> ClassifierRequest:
    if item.kind == "user":
        return ClassifierRequest(
            [
                {"role": "system", "content": config["system_prompts"]["user_input"]},
                {"role": "user", "content": item.text},
            ],
            None,
            None,
        )
    return ClassifierRequest(
        [
            {"role": "system", "content": config["system_prompts"]["tool_result"]},
            {"role": "user", "content": config["tool_notice"]},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_v2_0",
                        "type": "function",
                        "function": {"name": "read_document", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_v2_0", "content": item.text},
        ],
        [
            {
                "type": "function",
                "function": {
                    "name": "read_document",
                    "description": "도구 결과를 읽는다",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        "none",
    )


def parse_label(
    output: str | None, finish_reason: str | None, config: Mapping
) -> Literal["safe", "unsafe"] | None:
    if finish_reason == "length" or not isinstance(output, str) or not output.strip():
        return None
    for label in ("safe", "unsafe"):
        if output.strip() == config["labels"][label]:
            return label
    return None


def apply_quarantine(
    messages: Sequence[Mapping], flagged: Mapping[tuple[int, tuple], bool]
) -> tuple[list[dict], list[str]]:
    result = [dict(message) for message in messages]
    ids = []
    for index, message in enumerate(messages):
        if message.get("role") != "tool" or not isinstance(message.get("content"), str):
            continue
        content = message["content"]
        values = _json_strings(content)
        replacements = []
        if values is None:
            if flagged.get((index, ())) and not _marker_only(content):
                replacements.append((0, len(content), QUARANTINE_MARKER))
        else:
            for path, (text, start, end) in values.items():
                if flagged.get((index, path)) and not _marker_only(text):
                    replacements.append(
                        (start, end, json.dumps(QUARANTINE_MARKER, ensure_ascii=False))
                    )
        if not replacements:
            continue
        for start, end, replacement in sorted(replacements, reverse=True):
            content = content[:start] + replacement + content[end:]
        result[index]["content"] = content
        call_id = message.get("tool_call_id")
        if valid_tool_call_id(call_id) and call_id not in ids:
            ids.append(call_id)
    return result, ids
