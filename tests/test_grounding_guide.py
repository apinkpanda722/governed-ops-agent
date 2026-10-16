"""The M8 guide is the source of label names; no evaluation data is opened."""

from pathlib import Path

from govops.grounding import sheets
from govops.grounding.grader import LABELS


def test_m8_guide_contract():
    guide = (
        Path(__file__).resolve().parents[1] / "docs/HUMAN_GRADING_GUIDE_M8.md"
    ).read_text()
    assert all(f"## {i}." in guide for i in range(1, 8))
    assert "예시" in guide
    assert all(f"`{label}`" in guide for label in LABELS)
    assert "label" in sheets.LABEL_COLUMNS and "`label` 칸" in guide
    assert "supports" in sheets.CITATION_COLUMNS
    assert all(
        word in guide
        for word in (
            "supports",
            "지지",
            "비지지",
            "make m8-grounding-register",
            "4~5.5시간",
        )
    )
