"""Read-only regrading of the preserved dev evidence."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from govops.eval.observation import load_observations
from govops.evalrun import regrade
from govops.evalrun.explore import candidate, longest_streak

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "data/results/dev/da337bb7471c4bceae14a2e9b3ce5884"


@pytest.mark.parametrize(
    "run,inputs,expected_candidate",
    [
        (
            "e36589778c2243388f09d9b4d3b60f2b",
            {
                "T1-01": 1,
                "T1-05": 0,
                "T1-08": 1,
                "T2-01": 1,
                "T2-02": 3,
                "T2-03": 0,
                "T3-01": 0,
                "T3-02": 1,
                "T3-07": 0,
                "T3-08": 1,
                "T4-01": 1,
                "T4-06": 1,
                "T5-01": 1,
                "T5-02": 0,
                "T5-05": 1,
                "T5-08": 1,
                "T5-11": 1,
                "T6-01": 1,
                "T6-03": 1,
            },
            4,
        ),
        (
            "d2cd927c073447728c76c040d7bfb3bc",
            {
                "T1-01": 1,
                "T1-05": 0,
                "T1-08": 0,
                "T2-01": 1,
                "T2-02": 2,
                "T2-03": 0,
                "T3-02": 1,
                "T3-07": 0,
                "T3-08": 1,
                "T4-01": 1,
                "T4-06": 1,
                "T5-01": 0,
                "T5-02": 0,
                "T5-05": 1,
                "T5-08": 1,
                "T5-11": 1,
                "T6-03": 1,
            },
            3,
        ),
        (
            "710d8b6e56794dca8e4b027d47b8b5bf",
            {
                "T1-01": 1,
                "T1-05": 0,
                "T1-08": 0,
                "T2-01": 1,
                "T2-02": 2,
                "T2-03": 0,
                "T3-01": 1,
                "T3-02": 1,
                "T3-07": 1,
                "T3-08": 1,
                "T4-01": 1,
                "T4-06": 1,
                "T5-01": 1,
                "T5-02": 1,
                "T5-05": 1,
                "T5-08": 1,
                "T5-11": 1,
                "T6-03": 1,
            },
            3,
        ),
        (
            "02ef5d5a64394671b3c99767c38e4e17",
            {
                "T1-01": 1,
                "T1-05": 0,
                "T1-08": 0,
                "T2-01": 1,
                "T2-02": 2,
                "T2-03": 0,
                "T3-02": 1,
                "T3-07": 0,
                "T3-08": 1,
                "T4-01": 1,
                "T4-06": 1,
                "T5-01": 1,
                "T5-02": 1,
                "T5-05": 1,
                "T5-08": 1,
                "T5-11": 1,
                "T6-01": 1,
                "T6-03": 1,
            },
            3,
        ),
    ],
)
def test_n_candidate_preserved_dev_runs(run, inputs, expected_candidate):
    directory = ROOT / "data/results/dev" / run
    rules = json.loads((directory / "manifest.json").read_text())["grading_rules"]
    result = regrade.n_candidate(directory, rules=rules)
    assert result["inputs"] == inputs
    assert result["candidate"] == expected_candidate
    assert result["excluded_finalized_tasks"] == []


@pytest.mark.parametrize("include_mismatch", [False, True])
def test_n_candidate_excludes_finalized_successes(
    tmp_path, monkeypatch, include_mismatch
):
    (tmp_path / "manifest.json").write_text('{"tool_trace_mismatch": {"mismatch": []}}')
    tasks = [
        ("normal", None, 2),
        ("z-final", "max_steps_finalized", 20),
        ("a-final", "max_steps_finalized", 12),
        ("mismatch", None, 3),
    ]
    rows = [
        SimpleNamespace(
            task_id=task,
            stop_reason=reason,
            tool_results=[
                {"name": "jira_get_ticket", "ok": True, "result": {}}
                for _ in range(count)
            ],
        )
        for task, reason, count in tasks
    ]
    monkeypatch.setattr(regrade, "load_observations", lambda _: rows)
    monkeypatch.setattr(
        regrade,
        "regrade_run",
        lambda *args: {
            task: SimpleNamespace(tcr=SimpleNamespace(passed=True))
            for task, _, _ in tasks
        },
    )
    result = regrade.n_candidate(tmp_path, include_mismatch=include_mismatch)
    assert result["inputs"] == (
        {"normal": 2, "mismatch": 3} if include_mismatch else {"normal": 2}
    )
    assert result["candidate"] == (4 if include_mismatch else 3)
    assert result["excluded_finalized_tasks"] == ["a-final", "z-final"]
    assert result["excluded_mismatch_tasks"] == (
        [] if include_mismatch else ["mismatch"]
    )


def test_registered_matches_saved_grades(capsys):
    grades = regrade.regrade_run(RUN, "registered")
    saved = json.loads((RUN / "grades.json").read_text())
    assert len(grades) == 20
    assert sum(g.tcr.passed for g in grades.values()) == 17
    assert {k for k, g in grades.items() if not g.tcr.passed} == {
        "T2-01",
        "T2-09",
        "T4-06",
    }
    for key, grade in grades.items():
        assert grade.tcr.passed == saved[key]["tcr"]["passed"]
        assert list(grade.tcr.reasons) == saved[key]["tcr"]["reasons"]
    regrade.main(["--run", str(RUN), "--rules", "registered"])
    output = capsys.readouterr().out
    assert output.startswith("참고값: 공식 결과 아님\n")
    assert "TCR 17/20" in output
    assert "T2-01: registered 실패(forbidden_leak), 노출 후보 1" in output


def test_m3_changes_only_t2_01_without_guard(monkeypatch, capsys):
    from govops.eval import run_grade

    monkeypatch.setattr(
        run_grade, "guard_grading_rules", lambda *a: pytest.fail("guard")
    )
    registered = regrade.regrade_run(RUN, "registered")
    updated = regrade.regrade_run(RUN, "m3-grading")
    changed = [k for k in registered if registered[k].tcr != updated[k].tcr]
    assert changed == ["T2-01"], f"unexpected changed tasks: {changed}"
    assert sum(g.tcr.passed for g in updated.values()) == 18
    regrade.main(
        ["--run", str(RUN), "--rules", "m3-grading", "--compare", "registered"]
    )
    output = capsys.readouterr().out
    assert "TCR 18/20" in output and "TCR 17/20" in output
    assert "T2-01: registered 실패(forbidden_leak) → m3-grading 통과" in output
    assert "판정 변경 task: T2-01" in output
    path = "docs/results/hypotheses-m3-grading.md"
    assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() in output
    commit = subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", path], cwd=ROOT, text=True, timeout=30
    ).strip()
    assert commit and commit in output


def test_n_candidate_excludes_saved_mismatches(capsys):
    rows = [o.model_dump() for o in load_observations(RUN / "observations.jsonl")]
    saved = json.loads((RUN / "grades.json").read_text())
    mismatch = set(
        json.loads((RUN / "manifest.json").read_text())["tool_trace_mismatch"]
    )
    assert mismatch == {"T2-09", "T5-11", "T6-01"}
    for include in (False, True):
        result = regrade.n_candidate(RUN, include_mismatch=include)
        passed = {
            k: g["tcr"]["passed"] and (include or k not in mismatch)
            for k, g in saved.items()
        }
        inputs = {
            r["task_id"]: longest_streak(
                [
                    c
                    for c in r["tool_results"]
                    if c["name"]
                    not in {
                        "slack_send_message",
                        "jira_add_comment",
                        "jira_create_followup_ticket",
                    }
                ]
            )
            for r in rows
            if passed[r["task_id"]]
        }
        filtered = [
            r
            | {
                "tool_results": [
                    c
                    for c in r["tool_results"]
                    if c["name"]
                    not in {
                        "slack_send_message",
                        "jira_add_comment",
                        "jira_create_followup_ticket",
                    }
                ]
            }
            for r in rows
        ]
        assert result["inputs"] == inputs
        assert result["candidate"] == candidate(filtered, passed)
        assert len(inputs) == (17 if include else 15)
        regrade.main(
            ["--run", str(RUN), "--rules", "registered", "--n"]
            + (["--include-mismatch"] if include else [])
        )
        assert "잠정치이며 등록값이 아님" in capsys.readouterr().out


def test_cli_required_rules_and_data_unchanged():
    def hashes():
        return {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (ROOT / "data").rglob("*")
            if p.is_file()
        }

    before = hashes()
    for args in (
        ["--rules", "registered"],
        [
            "--rules",
            "m3-grading",
            "--compare",
            "registered",
            "--n",
            "--include-mismatch",
        ],
    ):
        result = subprocess.run(
            [sys.executable, "-m", "govops.evalrun.regrade", "--run", str(RUN), *args],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stderr
        assert not result.stderr
    assert hashes() == before
    result = subprocess.run(
        [sys.executable, "-m", "govops.evalrun.regrade", "--run", str(RUN)],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=90,
    )
    assert result.returncode == 2
