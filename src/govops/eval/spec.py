"""Declarative evaluation expectations and safe invariant diagnostics.

Body quality belongs to accuracy scoring, not structural completion criteria.
"""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from govops.eval.meta import EvalMeta
from govops.eval.visibility import can_read

TaskType = Literal["T1", "T2", "T3", "T4", "T5", "T6"]
_TOOL_NAMES = frozenset(
    {
        "search_docs",
        "lookup_id",
        "run_sql",
        "get_document",
        "jira_get_ticket",
        "jira_search_tickets",
        "jira_add_comment",
        "jira_create_followup_ticket",
        "slack_send_message",
    }
)
_BOUNDARY = {"no_permission", "nonexistent", "indirect_injection", "other_team_channel"}
_APPROVAL = {"reject", "edit_approve", "other_user_approve", "all_hands_approver"}


class SpecValidationError(ValueError):
    """Only a safe task ID and invariant category are reported."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Effect(_Model):
    kind: Literal["slack_message", "jira_comment", "jira_followup_ticket"]
    target: str
    count: int = Field(gt=0)
    structure: dict[str, str]


class ApprovalStep(_Model):
    actor: Literal["requester", "other_team_user", "same_team_g4"]
    decision: Literal["approve", "reject", "edit_approve"]
    expect_http: int | None


class Expected(_Model):
    outcome: Literal[
        "answer", "not_found", "blocked", "approval_rejected", "approved_executed"
    ]
    effects: list[Effect]


class Forbidden(_Model):
    leak_doc_ids: list[str]
    leak_ticket_ids: list[str]
    leak_values: list[str]
    forbidden_tools: list[str]


class RequiredFact(_Model):
    fact_id: str
    kind: str
    value: str
    mode: Literal["literal", "rubric_only"]


class DerivedValue(_Model):
    name: str
    value: str


class Provenance(_Model):
    meta_sha256: str
    source_doc_ids: list[str]
    source_ticket_ids: list[str]


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


class TaskSpec(_Model):
    task_id: str
    type: TaskType
    subtype: str
    split: Literal["dev", "test"]
    executable_in: Literal["m2", "m3"]
    user: str
    request: str
    expected: Expected
    required_tools_any: list[list[str]]
    forbidden: Forbidden
    required_facts: list[RequiredFact]
    derived_values: list[DerivedValue]
    approval_script: list[ApprovalStep]
    provenance: Provenance
    spec_sha256: str


def spec_hash(spec: TaskSpec) -> str:
    return hashlib.sha256(
        _json_bytes(spec.model_dump(exclude={"spec_sha256"}))
    ).hexdigest()


def _fail(spec: TaskSpec, category: str) -> None:
    safe_id = spec.task_id if re.fullmatch(r"T[1-6]-\d+", spec.task_id) else "unknown"
    raise SpecValidationError(f"{safe_id}: {category}")


def validate_specs(
    specs: Sequence[TaskSpec],
    meta: EvalMeta,
    users: Sequence[Mapping[str, Any]],
    tickets: Sequence[Mapping[str, Any]],
) -> None:
    docs = {key: doc.model_dump() for key, doc in meta.doc_by_id.items()}
    ticket_index = {ticket["ticket_id"]: ticket for ticket in tickets}
    user_index = {user["label"]: user for user in users}
    facts = meta.fact_by_id
    meta_tickets = {ticket.ticket_id: ticket for ticket in meta.tickets}
    seen = set()
    for spec in specs:

        def check(condition: bool, category: str, current=spec) -> None:
            if not condition:
                _fail(current, category)

        check(bool(re.fullmatch(rf"{spec.type}-\d+", spec.task_id)), "task_id")
        check(spec.task_id not in seen, "duplicate_task_id")
        seen.add(spec.task_id)
        check(spec.spec_sha256 == spec_hash(spec), "spec_sha256")
        check(bool(re.fullmatch(r"[a-z][a-z0-9_]*", spec.subtype)), "subtype")
        check((spec.type == "T5") == (spec.subtype in _BOUNDARY), "subtype")
        check((spec.type == "T6") == (spec.subtype in _APPROVAL), "subtype")
        read_only = spec.type == "T1" or (
            spec.type == "T5" and spec.subtype != "other_team_channel"
        )
        check(spec.executable_in == ("m2" if read_only else "m3"), "executable_in")
        check(
            spec.executable_in != "m2"
            or (not spec.expected.effects and not spec.approval_script),
            "executable_in",
        )
        check(bool(spec.request.strip()) and len(spec.request) <= 300, "request_length")
        check(spec.user in user_index, "user_reference")
        check(
            spec.provenance.meta_sha256
            == hashlib.sha256(_json_bytes(meta.model_dump())).hexdigest(),
            "meta_sha256",
        )
        check(
            all(
                group and set(group) <= _TOOL_NAMES for group in spec.required_tools_any
            ),
            "tool_names",
        )
        check(set(spec.forbidden.forbidden_tools) <= _TOOL_NAMES, "tool_names")
        check(
            all(
                not set(group) <= set(spec.forbidden.forbidden_tools)
                for group in spec.required_tools_any
            ),
            "tool_conflict",
        )
        check(
            set(spec.provenance.source_doc_ids + spec.forbidden.leak_doc_ids)
            <= docs.keys(),
            "doc_reference",
        )
        check(
            set(spec.provenance.source_ticket_ids + spec.forbidden.leak_ticket_ids)
            <= ticket_index.keys(),
            "ticket_reference",
        )
        user = user_index[spec.user]

        def readable(row, identity=user):
            return can_read(
                identity,
                row["access_level"],
                row["access_teams"],
                row["access_min_grade"],
            )

        sources = [docs[key] for key in spec.provenance.source_doc_ids] + [
            ticket_index[key] for key in spec.provenance.source_ticket_ids
        ]
        leaks = [docs[key] for key in spec.forbidden.leak_doc_ids] + [
            ticket_index[key] for key in spec.forbidden.leak_ticket_ids
        ]
        check(not any(readable(row) for row in leaks), "leak_permission")
        outcome = spec.expected.outcome
        if outcome == "answer":
            check(any(readable(row) for row in sources), "source_permission")
            check(
                any(f.mode == "literal" for f in spec.required_facts)
                or bool(spec.derived_values),
                "required_literal",
            )
        if spec.subtype == "nonexistent":
            check(not sources and outcome == "not_found", "nonexistent_source")
            targets = re.findall(
                r"(?<![\w-])(?:INC-\d{4}-\d+|[A-Z]{3}-\d+|syn-[\w-]+)", spec.request
            )
            known = (
                set(docs)
                | set(ticket_index)
                | {f.source_id for f in meta.facts}
                | {t.incident_id for t in meta.tickets}
            )
            check(
                bool(targets) and not any(target in known for target in targets),
                "nonexistent_target",
            )
        elif outcome == "not_found":
            check(
                bool(sources) and not any(readable(row) for row in sources),
                "source_permission",
            )
            check(
                all(
                    any(readable(row, identity) for identity in users)
                    for row in sources
                ),
                "authorized_source",
            )
        if outcome in {"not_found", "blocked"}:
            check(not spec.required_facts and not spec.expected.effects, "outcome")
        if outcome in {"answer", "approval_rejected"}:
            check(not spec.expected.effects, "outcome")
        if outcome == "approved_executed":
            check(bool(spec.expected.effects), "outcome")
        if spec.type == "T4":
            check(
                bool(spec.derived_values)
                and outcome in {"approved_executed", "approval_rejected"},
                "aggregate",
            )
        check(all(item.value.strip() for item in spec.derived_values), "derived_value")
        allowed_facts = {
            fid
            for key in spec.provenance.source_doc_ids
            for fid in docs[key]["fact_ids"]
        }
        for key in spec.provenance.source_ticket_ids:
            if key in meta_tickets:
                ticket = meta_tickets[key]
                allowed_facts.update(ticket.fact_ids)
                allowed_facts.update(
                    f.fact_id
                    for f in meta.facts
                    if f.source_kind == "incident" and f.source_id == ticket.incident_id
                )
        for fact in spec.required_facts:
            reference = facts.get(fact.fact_id)
            check(
                reference is not None and fact.fact_id in allowed_facts,
                "fact_reference",
            )
            check(
                fact.value == reference.value and fact.kind == reference.kind,
                "fact_value",
            )
            check(
                fact.mode
                == (
                    "literal"
                    if fact.kind in {"id", "service", "datetime"}
                    else "rubric_only"
                ),
                "fact_mode",
            )
        check(
            not set(spec.forbidden.leak_values)
            & {f.value for f in spec.required_facts},
            "fact_conflict",
        )
        # IDs may identify a readable request target, but must not reveal a hidden source.
        hidden_ids = {key for key, row in docs.items() if not readable(row)} | {
            key for key, row in ticket_index.items() if not readable(row)
        }
        incident_rows: dict[str, list[Mapping[str, Any]]] = {}
        for row in ticket_index.values():
            if row.get("incident_id"):
                incident_rows.setdefault(row["incident_id"], []).append(row)
        for row in docs.values():
            for fid in row["fact_ids"]:
                fact = facts[fid]
                if fact.source_kind == "incident":
                    incident_rows.setdefault(fact.source_id, []).append(row)
        hidden_incidents = {
            key
            for key, rows in incident_rows.items()
            if not any(readable(row) for row in rows)
        }
        readable_targets = (
            {key for key, row in docs.items() if readable(row)}
            | {key for key, row in ticket_index.items() if readable(row)}
            | {
                key
                for key, rows in incident_rows.items()
                if any(readable(row) for row in rows)
            }
        )
        exposed_values = (
            {
                f.value
                for f in meta.facts
                if f.kind != "id" or f.value not in readable_targets
            }
            | {c.value for c in meta.canaries}
            | set(spec.forbidden.leak_values)
        )
        check(
            not any(
                value and value in spec.request
                for value in exposed_values | hidden_ids | hidden_incidents
            ),
            "request_answer_exposure",
        )
        for effect in spec.expected.effects:
            allowed_structure = (
                {"parent_ticket_id", "project_key"}
                if effect.kind == "jira_followup_ticket"
                else set()
            )
            check(set(effect.structure) <= allowed_structure, "effect_structure")


def load_specs(
    path: Path, meta: EvalMeta, users: Sequence[Mapping], tickets: Sequence[Mapping]
) -> list[TaskSpec]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise TypeError
        specs = []
        for item in raw:
            try:
                specs.append(TaskSpec.model_validate(item))
            except ValidationError:
                task_id = item.get("task_id") if isinstance(item, dict) else None
                safe_id = (
                    task_id
                    if isinstance(task_id, str) and re.fullmatch(r"T[1-6]-\d+", task_id)
                    else "unknown"
                )
                raise SpecValidationError(f"{safe_id}: schema") from None
        validate_specs(specs, meta, users, tickets)
        return specs
    except SpecValidationError:
        raise
    except (OSError, ValueError, TypeError):
        raise SpecValidationError("unknown: file") from None


def dump_specs(specs: Sequence[TaskSpec], path: Path) -> None:
    rows = [spec.model_dump() | {"spec_sha256": spec_hash(spec)} for spec in specs]
    path.write_bytes(_json_bytes(rows))
