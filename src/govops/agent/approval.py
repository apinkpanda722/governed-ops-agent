"""Pure approval policy for trusted session identities and content-only edits."""

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from govops.agent.draft import Draft
from govops.agent.users import UserInfo


class Decision(StrEnum):
    ALLOW = "allow"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"


EDITABLE_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "slack_send_message": frozenset({"text"}),
        "jira_add_comment": frozenset({"body"}),
        "jira_create_followup_ticket": frozenset({"title", "description"}),
    }
)
ANNOUNCE_APPROVER_MIN_GRADE = 4
_MAX_EDIT_LENGTH = 4000


class InvalidEdit(Exception):
    """An edit contains a forbidden field or invalid content value."""


def approver_decision(
    draft: Draft, *, requester: UserInfo, approver: UserInfo | None
) -> Decision:
    """Apply policy using identities resolved by the caller's session layer."""
    if approver is None:
        return Decision.NOT_FOUND
    is_requester = approver.label == requester.label
    if draft.tool == "slack_send_message" and draft.args["channel"] == "announce":
        if is_requester:
            return Decision.FORBIDDEN
        if (
            approver.team == requester.team
            and approver.grade >= ANNOUNCE_APPROVER_MIN_GRADE
        ):
            return Decision.ALLOW
        return Decision.NOT_FOUND
    return Decision.ALLOW if is_requester else Decision.NOT_FOUND


def apply_edit(draft: Draft, edited: Mapping[str, str]) -> Draft:
    """Return a new draft; the graph must rerun deterministic checks afterward."""
    if any(
        field not in EDITABLE_FIELDS[draft.tool]
        or not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_EDIT_LENGTH
        for field, value in edited.items()
    ):
        raise InvalidEdit("Invalid content edit")
    return Draft(draft.tool, {**draft.args, **edited})


def approval_id(task_id: str, draft_sha256: str) -> str:
    """Bind an opaque deterministic identifier to a task and its draft hash."""
    canonical = json.dumps(
        {"task_id": task_id, "draft_sha256": draft_sha256},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
