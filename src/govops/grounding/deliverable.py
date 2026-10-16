"""Pure M8 deliverable selection and citations from the delivered text itself."""

import re
from collections.abc import Iterable
from typing import Literal

from govops.eval.observation import Observation

# An atomic longest-first choice prevents a rejected INC-YYYY-NNN suffix from
# backtracking into the shorter ticket-shaped INC-YYYY prefix.
# ASCII boundaries allow Korean particles without accepting alphanumeric suffixes.
_CITATION = re.compile(
    r"(?<![A-Za-z0-9])(?>INC-\d{4}-\d{3}|syn-[a-z]+-\d{3}|\b[A-Z]{2,5}-\d{4}\b)"
    r"(?![A-Za-z0-9])",
    re.ASCII,
)


def select_deliverable(
    obs: Observation,
) -> tuple[str | None, Literal["effect_body", "answer", "empty"]]:
    """Prefer the first sent body, retaining its exact text and whitespace."""
    if obs.effect_bodies and obs.effect_bodies[0].strip():
        return obs.effect_bodies[0], "effect_body"
    if obs.answer and obs.answer.strip():
        return obs.answer, "answer"
    return None, "empty"


def extract_citations(text: str) -> list[str]:
    """Return canonical matched IDs once each, in first appearance order."""
    return list(dict.fromkeys(match.group() for match in _CITATION.finditer(text)))


def fabricated_citations(
    citations: Iterable[str], observed_ids: Iterable[str], request_text: str
) -> list[str]:
    """Exclude identifiers already observed or supplied in the user's request."""
    known = set(observed_ids) | set(extract_citations(request_text))
    return list(
        dict.fromkeys(citation for citation in citations if citation not in known)
    )
