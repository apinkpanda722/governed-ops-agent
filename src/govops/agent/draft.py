"""Immutable side-effect drafts and canonical content hashes."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from govops.tools.markers import _normalize

_FIELDS = {
    "slack_send_message": ("channel", ("text",)),
    "jira_add_comment": ("ticket_id", ("body",)),
    "jira_create_followup_ticket": ("parent_ticket_id", ("title", "description")),
}


@dataclass(frozen=True)
class Draft:
    tool: str
    args: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.tool not in _FIELDS:
            raise ValueError("Draft requires a side-effect tool")
        args = _normalize(self.args)
        target, fields = _FIELDS[self.tool]
        allowed = {target, *fields}
        if self.tool == "jira_create_followup_ticket":
            allowed.add("priority")
        if (
            set(args) - allowed
            or any(not isinstance(args.get(key), str) for key in (target, fields[0]))
            or any(
                value is not None and not isinstance(value, str)
                for value in args.values()
            )
        ):
            raise ValueError("Invalid draft arguments")
        object.__setattr__(self, "args", MappingProxyType(args))


def draft_sha256(draft: Draft) -> str:
    canonical = json.dumps(
        {"tool": draft.tool, "args": _normalize(draft.args)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def target_of(draft: Draft) -> str:
    return draft.args[_FIELDS[draft.tool][0]]


def body_text(draft: Draft) -> str:
    return "\n".join(
        draft.args[field] for field in _FIELDS[draft.tool][1] if draft.args.get(field)
    )
