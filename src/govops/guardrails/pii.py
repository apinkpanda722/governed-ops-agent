"""Format-based Korean PII detection; offsets refer to the original text."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class PiiMatch:
    kind: str
    start: int
    end: int


_PATTERNS = {
    "rrn": r"(?<![0-9])[0-9]{6}-?[0-9]{7}(?![0-9])",
    "mobile": r"(?<![0-9])01[016789][ -]?[0-9]{3,4}[ -]?[0-9]{4}(?![0-9])",
    "landline": r"(?<![0-9])(?:02|0[3-6][1-5]|070)[ -]?[0-9]{3,4}[ -]?[0-9]{4}(?![0-9])",
    "email": r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w-])",
    "card": r"(?<![0-9])[0-9]{13,19}(?![0-9])|(?<![0-9])(?:[0-9]{4}[ -]){3,4}[0-9]{1,4}(?![0-9])",
    "biz_reg": r"(?<![0-9])[0-9]{3}-[0-9]{2}-[0-9]{5}(?![0-9])",
    "account": r"(?<![0-9])[0-9]{3,6}-[0-9]{2,6}-[0-9]{4,8}(?![0-9])",
    "passport": r"(?<![A-Za-z0-9])[A-Za-z][0-9]{8}(?![A-Za-z0-9])",
}
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—−﹘﹣－"), "-")


def _normalized(text: str) -> tuple[str, list[int]]:
    chars, offsets = [], []
    for index, char in enumerate(text):
        normalized = unicodedata.normalize("NFKC", char).translate(_DASHES)
        chars.extend(normalized)
        offsets.extend([index] * len(normalized))
    return "".join(chars), offsets


def _valid(kind: str, value: str) -> bool:
    digits = re.sub(r"[^0-9]", "", value)
    if kind == "rrn" and "-" not in value:
        try:
            date(2000 + int(digits[:2]), int(digits[2:4]), int(digits[4:6]))
        except ValueError:
            return False
    if kind == "card":
        if not 13 <= len(digits) <= 19:
            return False
        total = 0
        for index, char in enumerate(reversed(digits)):
            number = int(char) * (2 if index % 2 else 1)
            total += number - 9 if number > 9 else number
        return total % 10 == 0
    return True


def find_pii(text: str) -> list[PiiMatch]:
    normalized, offsets = _normalized(text)
    candidates = []
    for kind, pattern in _PATTERNS.items():
        for match in re.finditer(pattern, normalized):
            if _valid(kind, match.group()):
                candidates.append(
                    PiiMatch(kind, offsets[match.start()], offsets[match.end() - 1] + 1)
                )
    selected = []
    for candidate in sorted(candidates, key=lambda m: (-(m.end - m.start), m.start)):
        if not any(
            candidate.start < other.end and other.start < candidate.end
            for other in selected
        ):
            selected.append(candidate)
    return sorted(selected, key=lambda m: m.start)


def mask_pii(text: str, *, placeholder: str = "[PII:{kind}]") -> str:
    for match in reversed(find_pii(text)):
        text = (
            text[: match.start]
            + placeholder.format(kind=match.kind)
            + text[match.end :]
        )
    return text
