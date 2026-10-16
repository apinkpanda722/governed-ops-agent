"""Preregistered sent-v1 rules shared by human labels and the M8 grader."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from govops.grounding.deliverable import extract_citations

SPLITTER_RULES_VERSION = "sent-v1"

_LEADING_MARKER = re.compile(r"^(?:[-*•·▪‣]|\d{1,2}[.)]|\(\d{1,2}\)|#{1,6})(?:\s+|$)")
_PUNCTUATION_END = re.compile(r"[.?!。][\"'”’)」』\]]*(?=\s|$)")
_KOREAN_END = re.compile(
    r"(?<=[다요죠까])\s+"
    r"(?!(?:고|라고|하고|며|면서|는데|지만|니까|서)(?=\s|$))"
    r"(?=[가-힣A-Z0-9\[(])"
)


@dataclass(frozen=True)
class Sentence:
    sentence_id: str
    text: str
    citations: tuple[str, ...]


def split_sentences(text: str) -> list[str]:
    """Apply S1 lines, S2 markers, S3 punctuation, S4 endings, then S5 spaces."""
    result: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _LEADING_MARKER.sub("", line, count=1)
        start = 0
        # Punctuation belongs to the preceding sentence, including closing marks.
        ends = [match.end() for match in _PUNCTUATION_END.finditer(line)]
        for end in [*ends, len(line)]:
            for fragment in _KOREAN_END.split(line[start:end]):
                cleaned = " ".join(fragment.split())
                if cleaned:
                    result.append(cleaned)
            start = end
    return result


def build_sentences(text: str) -> list[Sentence]:
    """Assign consecutive sentence keys and citations from each sentence's text."""
    return [
        Sentence(f"s{index}", sentence, tuple(extract_citations(sentence)))
        for index, sentence in enumerate(split_sentences(text), start=1)
    ]


def splitter_rules_sha256() -> str:
    """Hash this module's source bytes so registration can detect any change."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
