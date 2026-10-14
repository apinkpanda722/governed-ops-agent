"""The blind scoring guide matches code contracts without opening evaluation data."""

import json
import re
from pathlib import Path

from test_evalrun_dev_m3_m6 import spec

from govops.eval.grading import (
    DIMENSIONS,
    NO_PERMISSION_CRITERIA,
    build_prompt,
    criteria_for,
)
from govops.eval.human import COLUMNS, HUMAN_SAMPLE_ALLOCATION

ROOT = Path(__file__).resolve().parents[1]


def test_guide_columns_criteria_scale_and_sample():
    guide = (ROOT / "docs/HUMAN_GRADING_GUIDE.md").read_text()
    assert ", ".join(COLUMNS) in guide
    assert all(name in guide for name in DIMENSIONS)
    assert sum(HUMAN_SAMPLE_ALLOCATION.values()) == 30
    assert "30개" in guide
    for number, meaning in json.loads(build_prompt(spec(), "합성"))["scale"].items():
        assert f"{number}점" in guide and meaning in guide
    assert all(meaning in guide for meaning in criteria_for(spec()).values())
    assert all(meaning in guide for meaning in NO_PERMISSION_CRITERIA.values())
    assert not re.search(r"T[1-6]-", guide)
    assert "합성 예시" in guide
    assert "UTF-8" in guide
    assert "docs/results/human-grading-registration.json" in guide
    assert "v1+v2" in guide and "κ" in guide
    assert "1회차" in guide


def test_guide_commands_exist_and_preserve_blind_order():
    guide = (ROOT / "docs/HUMAN_GRADING_GUIDE.md").read_text()
    makefile = (ROOT / "Makefile").read_text()
    commands = ["eval-human-sheet", "eval-human-register", "eval-gemini-grade"]
    for target in commands:
        assert f"make {target}" in guide
        assert f"\n{target}:\n" in makefile
    assert (
        "make eval-human-sheet OBS=data/results/test/<결과 디렉터리>/observations.jsonl"
        in guide
    )
    assert "data/eval/human/scores.csv" in guide
    assert "Gemini 점수를 보기 전에" in guide
    assert guide.index("make eval-human-register") < guide.index(
        "make eval-gemini-grade"
    )
