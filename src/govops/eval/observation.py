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
    status: Literal["completed", "failed", "rejected", "expired", "interrupted"]
    answer: str | None
    observed_ids: list[str]
    cited_doc_ids: list[str]
    tool_calls: list[ToolCallRecord]
    effects: list[ObservedEffect]
    effect_bodies: list[str]
    # 승인 직전 초안 본문(edit_approve 채점용). 없으면 수정본 표식만 본다.
    draft_bodies: list[str] = Field(default_factory=list)
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
