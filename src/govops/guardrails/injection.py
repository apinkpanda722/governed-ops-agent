"""Phrase detection with Unicode normalization and original source offsets."""

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from govops.guardrails.registry import RULES_FILE

CHUNK_SIZE = 20_000
_OVERLAP = 1024


@dataclass(frozen=True)
class Rule:
    id: str
    scope: tuple[str, ...]
    pattern: re.Pattern[str]
    family: str


@dataclass(frozen=True)
class InjectionMatch:
    rule_id: str
    start: int
    end: int


def load_rules(path: str | Path | None = None) -> tuple[Rule, ...]:
    try:
        raw = json.loads(Path(path or RULES_FILE).read_bytes())
        if (
            not isinstance(raw, dict)
            or set(raw) != {"version", "rules"}
            or type(raw["version"]) is not int
            or raw["version"] != 1
            or not isinstance(raw["rules"], list)
            or not raw["rules"]
        ):
            raise ValueError
        rules = []
        ids = set()
        for item in raw["rules"]:
            if not isinstance(item, dict) or set(item) != {
                "id",
                "scope",
                "pattern",
                "family",
            }:
                raise ValueError
            if (
                not isinstance(item["id"], str)
                or not re.fullmatch("[a-z0-9_]+", item["id"])
                or item["id"] in ids
            ):
                raise ValueError
            if (
                not isinstance(item["scope"], list)
                or not item["scope"]
                or any(s not in ("input", "tool") for s in item["scope"])
            ):
                raise ValueError
            if (
                not isinstance(item["family"], str)
                or not item["family"]
                or not isinstance(item["pattern"], str)
                or not item["pattern"]
            ):
                raise ValueError
            pattern = re.compile(item["pattern"], re.IGNORECASE)
            if pattern.search(""):
                raise ValueError
            rules.append(
                Rule(item["id"], tuple(item["scope"]), pattern, item["family"])
            )
            ids.add(item["id"])
        return tuple(rules)
    except (ValueError, TypeError, re.error, OSError):
        raise ValueError("Invalid rules") from None


@lru_cache(maxsize=1)
def _default_rules() -> tuple[Rule, ...]:
    return load_rules()


def _normalized(text: str) -> tuple[str, list[tuple[int, int]]]:
    # Canonical decomposition allows Hangul jamo to compose across source chars.
    decomposed = []
    for index, char in enumerate(text):
        if unicodedata.category(char) == "Cf":
            continue
        for part in unicodedata.normalize("NFKD", char).casefold():
            decomposed.append((part, index))
    chars, offsets = [], []
    cursor = 0
    while cursor < len(decomposed):
        end = cursor + 1
        value = decomposed[cursor][0]
        while end < len(decomposed):
            candidate = unicodedata.normalize("NFC", value + decomposed[end][0])
            if len(candidate) >= len(value) + 1 and not unicodedata.combining(
                decomposed[end][0]
            ):
                break
            value = candidate
            end += 1
        value = unicodedata.normalize("NFC", value)
        chars.extend(value)
        offsets.extend(
            [(decomposed[cursor][1], decomposed[end - 1][1] + 1)] * len(value)
        )
        cursor = end
    return "".join(chars), offsets


def find_injection(
    text: str, *, scope: Literal["input", "tool"], rules=None
) -> list[InjectionMatch]:
    if scope not in ("input", "tool"):
        raise ValueError("Invalid scope")
    active = _default_rules() if rules is None else rules
    normalized, offsets = _normalized(text)
    compact = [
        (char, offset)
        for char, offset in zip(normalized, offsets, strict=True)
        if not char.isspace()
    ]
    variants = [
        (normalized, offsets),
        ("".join(c for c, _ in compact), [o for _, o in compact]),
    ]
    found = set()
    for variant_index, (value, positions) in enumerate(variants):
        for rule in active:
            if scope not in rule.scope:
                continue
            # Removing whitespace requires the phrase separators to accept zero spaces.
            pattern = (
                re.compile(
                    rule.pattern.pattern.replace(r"\s+", r"\s*"), rule.pattern.flags
                )
                if variant_index == 1
                else rule.pattern
            )
            for base in range(0, len(value), CHUNK_SIZE - _OVERLAP):
                chunk = value[base : base + CHUNK_SIZE]
                for match in pattern.finditer(chunk):
                    if match.end() > match.start():
                        found.add(
                            (
                                rule.id,
                                positions[base + match.start()][0],
                                positions[base + match.end() - 1][1],
                            )
                        )
    return [
        InjectionMatch(*item)
        for item in sorted(found, key=lambda x: (x[1], x[2], x[0]))
    ]
