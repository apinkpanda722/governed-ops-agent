"""Read only trace outcome attributes; match executed reads as multisets."""

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReadSpan:
    name: str
    success: bool
    error_code: str | None


@dataclass(frozen=True)
class LinkResult:
    linked_reads: int
    unlinked_read: int
    error_codes: dict[str, int]


def read_tool_spans(trace_path: Path) -> list[ReadSpan]:
    """Raise ValueError for a malformed trace; callers treat it as unavailable.

    Bodies are never accessed. Attempts and task spans do not describe reads.
    """
    spans = []
    try:
        with trace_path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("invalid trace")  # noqa: TRY004 - malformed trace contract.
                if row.get("kind") != "tool_call" or row.get("name") == "tool_attempt":
                    continue
                name, attributes = row.get("name"), row.get("attributes")
                if not isinstance(name, str) or not isinstance(attributes, dict):
                    raise ValueError("invalid trace")  # noqa: TRY004 - malformed trace contract.
                success, error = attributes.get("success"), attributes.get("error_code")
                if type(success) is not bool or (
                    error is not None and not isinstance(error, str)
                ):
                    raise ValueError("invalid trace")
                spans.append(ReadSpan(name, success, error))
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("invalid trace")
    return spans


def link_reads(
    obs_read_calls: Sequence[tuple[str, bool]], spans: Sequence[ReadSpan]
) -> LinkResult:
    if Counter(obs_read_calls) != Counter((span.name, span.success) for span in spans):
        return LinkResult(0, len(obs_read_calls), {})
    return LinkResult(
        len(obs_read_calls),
        0,
        dict(
            sorted(
                Counter(
                    span.error_code for span in spans if span.error_code is not None
                ).items()
            )
        ),
    )
