"""Synthetic execution observations shared by offline graders and future runners."""

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class _Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True
    )


class ToolCallRecord(_Model):
    name: str
    ok: bool


class ObservedEffect(_Model):
    kind: Literal["slack_message", "jira_comment", "jira_followup_ticket"]
    target: str
    count: int = Field(ge=0)
    structure: dict[str, str]
    approval_id: str | None
    body_sha256: str | None


class ApprovalRecord(_Model):
    approval_id: str
    actor: str
    decision: str
    http_status: int


class Observation(_Model):
    task_id: str
    run_index: int = Field(ge=0)
    status: Literal[
        "completed", "failed", "rejected", "expired", "interrupted", "blocked_input"
    ]
    answer: str | None
    observed_ids: list[str]
    cited_doc_ids: list[str]
    tool_calls: list[ToolCallRecord]
    # Runner retains structural result IDs only, never tool bodies.
    tool_results: list[dict] = Field(default_factory=list)
    effects: list[ObservedEffect]
    effect_bodies: list[str]
    approvals: list[ApprovalRecord]
    policy_violations: list[str]
    fault_injected: bool
    trace_id: str | None


def dump_observations(path: Path, obs: Iterable[Observation]) -> None:
    path.write_text(
        "".join(
            json.dumps(row.model_dump(), ensure_ascii=False, sort_keys=True) + "\n"
            for row in obs
        ),
        encoding="utf-8",
    )


def load_observations(path: Path) -> list[Observation]:
    try:
        return [
            Observation.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
    except (ValidationError, ValueError, UnicodeError):
        raise ValueError("invalid observation schema") from None
