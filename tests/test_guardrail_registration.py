import hashlib
import json
import re
from pathlib import Path

import pytest

from govops.guardrails.registry import RULES_FILE, rules_sha256

ROOT = Path(__file__).resolve().parents[1]


def test_registration_fingerprint_and_no_patterns():
    text = (ROOT / "docs/results/hypotheses-m4-guardrail.md").read_text()
    assert re.findall(r"^rules_sha256: ([0-9a-f]{64})$", text, re.MULTILINE) == [
        rules_sha256()
    ]

    def strings(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"pattern", "regex"}:
                    yield item
                else:
                    yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)

    patterns = list(strings(json.loads(RULES_FILE.read_bytes())))
    assert patterns
    assert all(pattern not in text for pattern in patterns)


def test_supersedes_hashes():
    record = json.loads((ROOT / "docs/results/m4-dev3-supersedes.json").read_bytes())
    directory = ROOT / "data/results/dev" / record["previous_run_id"]
    for name, digest in record["previous_files"].items():
        path = ROOT / name if "/" in name else directory / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    manifest = json.loads((directory / "manifest.json").read_bytes())
    assert manifest["profile"] == "writes_org"
    assert manifest["grading_rules"] == "m3-grading"
    assert record["previous_official"] == {
        "grading_rules": "m3-grading",
        "tcr": "18/20",
    }


@pytest.mark.parametrize(
    "field", ["hypotheses_m4_guardrail_committed", "hypotheses_m4_guardrail_unchanged"]
)
def test_guardrail_registration_guard(field):
    from govops.evalrun.dev_m3 import guard_live_m3

    metadata = {
        key: True
        for key in (
            "hypotheses_m3_committed",
            "hypotheses_m3_unchanged",
            "hypotheses_m3_prompt_committed",
            "hypotheses_m3_prompt_unchanged",
            "hypotheses_m4_guardrail_committed",
            "hypotheses_m4_guardrail_unchanged",
            "worktree_clean",
            "guardrail_rules_registered",
        )
    }
    metadata[field] = False
    with pytest.raises(ValueError, match="^hypotheses_m4_guardrail_not_committed$"):
        guard_live_m3(metadata)


def test_unregistered_rules_guard():
    from govops.evalrun.dev_m3 import guard_live_m3

    metadata = {
        key: True
        for key in (
            "hypotheses_m3_committed",
            "hypotheses_m3_unchanged",
            "hypotheses_m3_prompt_committed",
            "hypotheses_m3_prompt_unchanged",
            "hypotheses_m4_guardrail_committed",
            "hypotheses_m4_guardrail_unchanged",
            "worktree_clean",
        )
    }
    with pytest.raises(ValueError, match="^guardrail_rules_not_registered$"):
        guard_live_m3(metadata)
