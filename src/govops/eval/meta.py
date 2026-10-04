"""Validated synthetic metadata, without document bodies or injection text."""

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from govops.mocks.auth import TEAMS

AccessLevel = Literal["public", "team", "restricted"]


class MetaValidationError(ValueError):
    """Diagnostics never interpolate source values."""


class _MetaModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class DocMeta(_MetaModel):
    doc_id: str
    doc_type: Literal["meeting", "weekly", "runbook", "guide", "incident", "notice"]
    team: str
    access_level: AccessLevel
    access_teams: list[str]
    access_min_grade: int
    fact_ids: list[str]
    related_doc_ids: list[str]
    injection_id: str | None = None
    canary_fact_id: str | None = None


class FactMeta(_MetaModel):
    fact_id: str
    kind: str
    value: str
    source_kind: Literal["incident", "event", "service", "canary"]
    source_id: str
    owner_team: str | None


class CanaryMeta(_MetaModel):
    canary_id: str
    fact_id: str
    value: str
    doc_id: str
    owner_team: str
    min_grade: int


class InjectionMeta(_MetaModel):
    injection_id: str
    doc_id: str
    variant: str
    access_level: AccessLevel


class TicketMeta(_MetaModel):
    ticket_id: str
    incident_id: str | None
    fact_ids: list[str]


class EvalMeta(_MetaModel):
    docs: list[DocMeta]
    facts: list[FactMeta]
    canaries: list[CanaryMeta]
    injections: list[InjectionMeta]
    tickets: list[TicketMeta]
    a_commit: str

    @property
    def doc_by_id(self) -> dict[str, DocMeta]:
        return {doc.doc_id: doc for doc in self.docs}

    @property
    def fact_by_id(self) -> dict[str, FactMeta]:
        return {fact.fact_id: fact for fact in self.facts}


def _message(problems: list[tuple[str, int, str]]) -> str:
    # 실패한 검사 이름과 건수만 담는다. 값·본문·ID는 담지 않는다.
    parts = [
        f"{name}: {count}" + (f" {unit}" if unit else "")
        for name, count, unit in problems
    ]
    return "invalid metadata schema or references: " + "; ".join(parts)


def _problems(meta: EvalMeta) -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []

    def add(name: str, count: int, unit: str = "") -> None:
        if count:
            found.append((name, count, unit))

    if not re.fullmatch(r"[0-9a-f]{40}", meta.a_commit):
        add("a_commit format", 1)
    for rows, key in (
        (meta.docs, "doc_id"),
        (meta.facts, "fact_id"),
        (meta.canaries, "canary_id"),
        (meta.injections, "injection_id"),
        (meta.tickets, "ticket_id"),
    ):
        ids = [getattr(row, key) for row in rows]
        add(f"duplicate {key}", len(ids) - len(set(ids)))
        add(f"empty {key}", sum(not value for value in ids))
    docs, facts = meta.doc_by_id, meta.fact_by_id
    injections = {item.injection_id: item for item in meta.injections}
    canaries = {item.fact_id: item for item in meta.canaries}
    add(
        "doc_id without syn- prefix",
        sum(not d.doc_id.startswith("syn-") for d in meta.docs),
        "docs",
    )
    add("doc team outside TEAMS", sum(d.team not in TEAMS for d in meta.docs), "docs")
    add(
        "doc access_teams outside TEAMS",
        sum(not set(d.access_teams) <= TEAMS for d in meta.docs),
        "docs",
    )
    add(
        "doc access_min_grade negative",
        sum(d.access_min_grade < 0 for d in meta.docs),
        "docs",
    )
    add(
        "doc fact_ids reference missing facts",
        sum(not set(d.fact_ids) <= facts.keys() for d in meta.docs),
        "docs",
    )
    add(
        "doc related_doc_ids reference missing docs",
        sum(not set(d.related_doc_ids) <= docs.keys() for d in meta.docs),
        "docs",
    )
    add(
        "doc injection_id mismatch",
        sum(
            d.injection_id is not None
            and (
                d.injection_id not in injections
                or injections[d.injection_id].doc_id != d.doc_id
            )
            for d in meta.docs
        ),
        "docs",
    )
    add(
        "doc canary_fact_id mismatch",
        sum(
            d.canary_fact_id is not None
            and (
                d.canary_fact_id not in canaries
                or canaries[d.canary_fact_id].doc_id != d.doc_id
            )
            for d in meta.docs
        ),
        "docs",
    )
    add(
        "fact owner_team outside TEAMS",
        sum(f.owner_team is not None and f.owner_team not in TEAMS for f in meta.facts),
        "facts",
    )
    add(
        "canary inconsistent with its doc or fact",
        sum(
            c.doc_id not in docs
            or c.owner_team not in TEAMS
            or c.min_grade < 0
            or c.fact_id not in facts
            or facts[c.fact_id].source_kind != "canary"
            or facts[c.fact_id].value != c.value
            or docs[c.doc_id].canary_fact_id != c.fact_id
            for c in meta.canaries
        ),
        "canaries",
    )
    add(
        "injection inconsistent with its doc",
        sum(
            i.doc_id not in docs or docs[i.doc_id].injection_id != i.injection_id
            for i in meta.injections
        ),
        "injections",
    )
    add(
        "ticket fact_ids reference missing facts",
        sum(not set(t.fact_ids) <= facts.keys() for t in meta.tickets),
        "tickets",
    )
    return found


def validate_meta(payload: dict) -> EvalMeta:
    try:
        meta = EvalMeta.model_validate(payload)
    except ValidationError as error:
        raise MetaValidationError(
            _message([("schema", len(error.errors()), "errors")])
        ) from None
    except (ValueError, TypeError):
        raise MetaValidationError(_message([("schema", 1, "errors")])) from None
    problems = _problems(meta)
    if problems:
        raise MetaValidationError(_message(problems))
    return meta


def load_meta(path: Path) -> EvalMeta:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raise MetaValidationError("invalid metadata file") from None
    return validate_meta(raw)


def meta_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
