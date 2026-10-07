"""Exact ledger exemptions and immutable start snapshots."""

import hashlib
import json
from pathlib import Path

import pytest

from govops.agent.checks import REASONS
from govops.evalrun.dev_m3 import check_reason
from govops.evalrun.worktree import dirty_entries, ledger_state

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def configured(tmp_path):
    target = tmp_path / "scripts/exempt_paths.json"
    target.parent.mkdir()
    target.write_bytes((ROOT / "scripts/exempt_paths.json").read_bytes())
    return tmp_path


def ledger_path(root):
    return json.loads((root / "scripts/exempt_paths.json").read_text())["ledger_paths"][
        0
    ]


def test_exact_exemption_and_rename(configured):
    ledger = ledger_path(configured)
    for code in (" M", "M ", "??", " D", "A "):
        assert dirty_entries(f"{code} {ledger}\n", configured) == []
    assert dirty_entries(f"R  old -> {ledger}", configured) == []
    for path in (
        ledger + ".bak",
        ledger + "/child",
        "prefix/" + ledger,
        "data/manifest/costs_grading.jsonl",
        "docs/a",
        "data/results/a",
        "data/replay/a",
        "phases/a",
    ):
        entry = f" M {path}"
        assert dirty_entries(entry, configured) == [entry]
    entry = f"R  {ledger} -> docs/a"
    assert dirty_entries(entry, configured) == [entry]
    assert dirty_entries(f' M "{ledger}"', configured) == []


@pytest.mark.parametrize(
    "value",
    [
        None,
        "{",
        "[]",
        "{}",
        '{"ledger_paths": "x"}',
        '{"ledger_paths": []}',
        '{"ledger_paths": [1]}',
        '{"ledger_paths": ["../escape"]}',
    ],
)
def test_invalid_config_fails_closed(tmp_path, value):
    target = tmp_path / "scripts/exempt_paths.json"
    target.parent.mkdir()
    if value is not None:
        target.write_text(value)
    for function, args in (
        (dirty_entries, ("", tmp_path)),
        (ledger_state, (tmp_path,)),
    ):
        with pytest.raises(ValueError, match="^exempt_paths_invalid$"):
            function(*args)


def test_ledger_snapshot(configured):
    assert ledger_state(configured) == {"lines": 0, "sha256": None}
    path = configured / ledger_path(configured)
    path.parent.mkdir(parents=True)
    for content, lines in ((b"", 0), (b"{}\n{}\n", 2), (b"{}\n{}", 2)):
        path.write_bytes(content)
        assert ledger_state(configured) == {
            "lines": lines,
            "sha256": hashlib.sha256(content).hexdigest(),
        }


def test_agents_matches_single_source():
    paths = json.loads((ROOT / "scripts/exempt_paths.json").read_text())["ledger_paths"]
    assert len(paths) == 1
    text = (ROOT / "AGENTS.md").read_text()
    assert f"비용 원장 `{paths[0]}` 하나뿐이면 로컬 검증을 생략한다" in text
    assert text.count(paths[0]) == 1


@pytest.mark.parametrize("reason", sorted(REASONS))
def test_check_reason_precedence(reason):
    assert check_reason({"check": {"status": "blocked", "reason": reason}}) == reason
    assert check_reason({"check": {"status": "passed", "reason": reason}}) is None
    assert check_reason({"rejected_edit": {"reason": reason}}) == reason
    assert (
        check_reason(
            {
                "check": {"status": "blocked", "reason": reason},
                "rejected_edit": {"reason": "unknown"},
            }
        )
        == reason
    )


@pytest.mark.parametrize(
    "view",
    [
        {},
        {"check": None},
        {"check": {"status": "blocked", "reason": "합성 초안"}},
        {"rejected_edit": {"reason": "synthetic-id"}},
        {"check": {"status": "blocked", "reason": []}},
    ],
)
def test_check_reason_rejects_unknown(view):
    assert check_reason(view) is None
