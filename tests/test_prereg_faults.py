"""Prospective fault registration must match the frozen scenario contract."""

import re
import subprocess
from pathlib import Path

from govops.evalrun.fault_scenarios import POOL, SCENARIOS, SEED

ROOT = Path(__file__).resolve().parents[1]
PATH = "docs/results/hypotheses-m3-faults.md"


def test_scenario_table_matches_constants():
    text = (ROOT / PATH).read_text()
    rows = re.findall(r"^\| (F\d+) \| (\w+) \| (\d+) \| (.*?) \|$", text, re.MULTILINE)
    expected = []
    for s in SCENARIOS:
        e = s.expected
        conditions = (
            f"duplicates={e.duplicates}; missing={e.missing}; status={e.status}; "
            f"posts={e.posts[0]}..{e.posts[1]}; lookups={e.lookups[0]}..{e.lookups[1]}; "
            f"wait={str(e.wait).lower()}; wait_seconds={e.wait_seconds}; "
            f"assert_circuit={str(e.assert_circuit).lower()}; "
            f"absolute_rule={str(s.absolute_rule).lower()}"
        )
        expected.append((s.scenario, s.variant, str(s.n), conditions))
    assert rows == expected
    assert f"계획 {sum(s.n for s in SCENARIOS)}회" in text
    assert f"분모 {sum(s.n for s in SCENARIOS if s.absolute_rule)}회" in text
    assert str(SEED) in text
    assert all(task in text for task in POOL)


def test_registration_unchanged_after_first_commit():
    commits = subprocess.check_output(
        ["git", "log", "--diff-filter=A", "--format=%H", "--", PATH],
        cwd=ROOT,
        text=True,
    ).splitlines()
    if not commits:
        return  # The step executor commits a new registration after verification.
    original = subprocess.check_output(
        ["git", "show", f"{commits[-1]}:{PATH}"], cwd=ROOT
    )
    assert (ROOT / PATH).read_bytes() == original
