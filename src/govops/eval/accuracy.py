"""Deterministic inclusion of literal metadata facts, without semantic inference."""

import re
import unicodedata
from dataclasses import dataclass

from govops.eval.observation import Observation
from govops.eval.spec import TaskSpec

LITERAL_KINDS = frozenset({"id", "service", "datetime"})
# ISO date plus HH:MM, or unpadded Korean date plus the same HH:MM.
DATETIME_VARIANTS = ("YYYY-MM-DD + HH:MM", "YYYY년 M월 D일 + HH:MM")
_DATETIME = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2})(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?$"
)


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


@dataclass(frozen=True)
class FactInclusion:
    required: int
    included: int
    missing: tuple[str, ...]
    rate: float | None


def _contains(kind: str, value: str, text: str) -> bool:
    value = normalize(value)
    if not value:
        return False
    if value in text:
        return True
    match = _DATETIME.fullmatch(value) if kind == "datetime" else None
    if match:
        year, month, day, clock = match.groups()
        dates = (f"{year}-{month}-{day}", f"{year}년 {int(month)}월 {int(day)}일")
        return clock in text and any(date in text for date in dates)
    return False


def fact_inclusion(spec: TaskSpec, text: str) -> FactInclusion:
    targets = [
        (f.fact_id, f.kind, f.value)
        for f in spec.required_facts
        if f.kind in LITERAL_KINDS and f.mode == "literal"
    ]
    targets += [(f.name, "derived", f.value) for f in spec.derived_values]
    text = normalize(text)
    missing = tuple(
        fid for fid, kind, value in targets if not _contains(kind, value, text)
    )
    required = len(targets)
    included = required - len(missing)
    return FactInclusion(
        required, included, missing, included / required if required else None
    )


def deliverable_text(obs: Observation) -> str | None:
    if obs.answer and normalize(obs.answer):
        return obs.answer
    return obs.effect_bodies[0] if obs.effect_bodies else None
