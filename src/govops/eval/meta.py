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
    source_kind: Literal["incident", "event", "canary"]
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


def validate_meta(payload: dict) -> EvalMeta:
    try:
        meta = EvalMeta.model_validate(payload)
        if not re.fullmatch(r"[0-9a-f]{40}", meta.a_commit):
            raise ValueError
        for rows, key in (
            (meta.docs, "doc_id"),
            (meta.facts, "fact_id"),
            (meta.canaries, "canary_id"),
            (meta.injections, "injection_id"),
            (meta.tickets, "ticket_id"),
        ):
            ids = [getattr(row, key) for row in rows]
            if len(set(ids)) != len(ids) or any(not value for value in ids):
                raise ValueError
        docs, facts = meta.doc_by_id, meta.fact_by_id
        injections = {item.injection_id: item for item in meta.injections}
        canaries = {item.fact_id: item for item in meta.canaries}
        for doc in meta.docs:
            if (
                not doc.doc_id.startswith("syn-")
                or doc.team not in TEAMS
                or not set(doc.access_teams) <= TEAMS
                or doc.access_min_grade < 0
                or not set(doc.fact_ids) <= facts.keys()
                or not set(doc.related_doc_ids) <= docs.keys()
            ):
                raise ValueError
            if doc.injection_id is not None and (
                doc.injection_id not in injections
                or injections[doc.injection_id].doc_id != doc.doc_id
            ):
                raise ValueError
            if doc.canary_fact_id is not None and (
                doc.canary_fact_id not in canaries
                or canaries[doc.canary_fact_id].doc_id != doc.doc_id
            ):
                raise ValueError
        for fact in meta.facts:
            if fact.owner_team is not None and fact.owner_team not in TEAMS:
                raise ValueError
        for item in meta.canaries:
            if (
                item.doc_id not in docs
                or item.owner_team not in TEAMS
                or item.min_grade < 0
                or item.fact_id not in facts
                or facts[item.fact_id].source_kind != "canary"
                or facts[item.fact_id].value != item.value
                or docs[item.doc_id].canary_fact_id != item.fact_id
            ):
                raise ValueError
        for item in meta.injections:
            if (
                item.doc_id not in docs
                or docs[item.doc_id].injection_id != item.injection_id
            ):
                raise ValueError
        for ticket in meta.tickets:
            if not set(ticket.fact_ids) <= facts.keys():
                raise ValueError
        return meta
    except (ValidationError, ValueError, TypeError):
        raise MetaValidationError("invalid metadata schema or references") from None


def load_meta(path: Path) -> EvalMeta:
    try:
        return validate_meta(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        raise MetaValidationError("invalid metadata file") from None


def meta_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
