"""Offline rollout exercises use synthetic inputs and temporary artifacts only."""

import asyncio
import importlib
from pathlib import Path

import pytest

from govops.api import rollout_exercise as exercise
from govops.gateway.client import ClassifyResult
from govops.rollout import policy, store

ROOT = Path(__file__).resolve().parents[1]
PASS_LINES = "PASS exercise_pass_path\nPASS exercise_rollback_path\n"


@pytest.mark.timeout(180)
def test_both_paths_execute_and_keep_bodies_out_of_records(tmp_path, capsys):
    operational = ROOT / "data/interim/rollout"
    existed = operational.exists()
    result = asyncio.run(exercise.run_exercise(root=tmp_path))
    assert result == [
        ("exercise_pass_path", True, None),
        ("exercise_rollback_path", True, None),
    ]
    assert operational.exists() is existed
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert {path.parent.name for path in files} == {
        "exercise_pass_path",
        "exercise_rollback_path",
    }
    assert len(files) == 6
    content = "".join(path.read_text() for path in files)
    for body in (exercise.REQUEST, exercise.ANSWER):
        assert body not in content + captured.out + captured.err
    assert "demo-exercise-g" not in content
    passing = store.read_exposures(tmp_path / "exercise_pass_path")
    assigned = [row for row in passing if row["event"] == "assigned"]
    assert len(assigned) == 60
    assert sum(row["variant"] == "v1" for row in assigned) >= 20
    assert sum(row["variant"] == "v2" for row in assigned) >= 20
    assert not any(row["event"] == "rollback" for row in passing)
    stopped = tmp_path / "exercise_rollback_path"
    assert store.read_state(stopped).kill is True
    assert store.read_state(stopped).pct_bp == 0
    rows = store.read_exposures(stopped)
    assert sum(row["event"] == "rollback" for row in rows) == 1
    assigned = [row for row in rows if row["event"] == "assigned"]
    assert [row["variant"] for row in assigned] == ["v2"] * 20 + ["v1"]
    assert len(store.read_classify_tail(stopped)) == 21


@pytest.mark.timeout(180)
def test_main_exact_output_and_operating_environment_ignored(
    monkeypatch, tmp_path, capsys
):
    for name in ("GOVOPS_ROLLOUT_DIR", "AUDIT_DIR", "GOVOPS_DATABASE_URL"):
        monkeypatch.setenv(name, str(tmp_path / name))
    assert exercise.main([]) == 0
    captured = capsys.readouterr()
    assert captured.out == PASS_LINES
    assert captured.err == ""
    assert list(tmp_path.iterdir()) == []


@pytest.mark.timeout(180)
def test_rollback_cannot_pass_with_healthy_classifier(monkeypatch, capsys):
    async def healthy(self, **kwargs):
        return ClassifyResult("safe", "ok", 0, 0, 0.0)

    monkeypatch.setattr(exercise.FakeClassifier, "classify", healthy)
    assert exercise.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == (
        "PASS exercise_pass_path\nFAIL exercise_rollback_path rollback_missing\n"
    )
    assert captured.err == ""


@pytest.mark.timeout(180)
def test_pass_path_detects_unexpected_rollback(monkeypatch, capsys):
    monkeypatch.setattr(policy, "ABNORMAL_RATE_MAX", -1.0)
    assert exercise.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == (
        "FAIL exercise_pass_path kill_unexpected\nPASS exercise_rollback_path\n"
    )
    assert captured.err == ""


@pytest.mark.timeout(180)
def test_documented_module_and_recipe_are_executable():
    document = (ROOT / "docs/results/hypotheses-m8-rollout.md").read_text()
    module = "govops.api.rollout_exercise"
    assert f"python -m {module}" in document
    assert "make m8-rollout-exercise" in document
    assert callable(importlib.import_module(module).main)
    recipe = (ROOT / "Makefile").read_text().split("\nm8-rollout-exercise:\n", 1)[1]
    assert recipe.split("\n\n", 1)[0].splitlines() == [f"\tuv run python -m {module}"]
