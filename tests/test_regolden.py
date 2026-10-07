"""Golden grading uses the registered entry point and preserves recording evidence."""

import json
from pathlib import Path

from govops.eval.meta import load_meta
from govops.eval.observation import Observation
from govops.eval.run_grade import grade_run
from govops.eval.tcr import grade_tcr
from govops.golden import GOLDEN_TASKS, load_golden_specs, tcr_status

ROOT = Path(__file__).resolve().parents[1]
RUN2 = ROOT / "data/results/dev/26c8553905bb4396b20ef2b2ff6399f1"
# Guardrail v1 재실행(m4-guardrail): 현재 골든 기록(status.json·llm·a_run)을 만든 실행.
RUN3 = ROOT / "data/results/dev/e36589778c2243388f09d9b4d3b60f2b"
RUN = ROOT / "data/results/dev/da337bb7471c4bceae14a2e9b3ce5884"


def grading_data():
    return {
        "meta": load_meta(ROOT / "data/eval/meta.json"),
        "tickets": json.loads((ROOT / "data/seed/tickets.json").read_text())["tickets"],
        "users": json.loads((ROOT / "data/seed/users.json").read_text())["users"],
    }


def observations(run=RUN):
    return {
        o.task_id: o
        for line in (run / "observations.jsonl").read_text().splitlines()
        if (o := Observation.model_validate_json(line))
    }


def test_saved_t2_01_uses_automatic_forbidden():
    spec = load_golden_specs()["T2-01"]
    obs = observations()["T2-01"]
    # The old direct grade_tcr path passed: unreadable IDs absent from the spec
    # are caught only through grade_run's automatic forbidden list.
    assert grade_tcr(spec, obs).passed
    assert (
        "forbidden_leak"
        in grade_run(
            spec=spec, obs=obs, **grading_data(), grading_rules="registered"
        ).tcr.reasons
    )
    assert (
        tcr_status(spec, obs, **grading_data(), grading_rules="registered")
        == "recorded_fail"
    )


def test_saved_golden_status_matches_all_fourteen_dev_grades():
    grades = json.loads((RUN3 / "grades.json").read_text())
    status = json.loads((ROOT / "data/replay/golden/status.json").read_text())
    assert len(GOLDEN_TASKS) == 14
    assert set(status) == set(GOLDEN_TASKS)
    # Guardrail v1 재실행에서는 T3-01도 통과해 14개 모두 pass다(이전 실행은 13 pass·T3-01 recorded_fail).
    assert sum(row["status"] == "pass" for row in status.values()) == 14
    assert sum(row["status"] == "recorded_fail" for row in status.values()) == 0
    for key in GOLDEN_TASKS:
        assert status[key]["eligible"]
        assert status[key]["profile"] == "writes_org"
        assert grades[key]["tcr"]["passed"] == (status[key]["status"] == "pass")


def test_regolden_dry_run_write_and_missing_observation(tmp_path, monkeypatch, capsys):
    from govops.evalrun import regolden

    status_path = tmp_path / "status.json"
    original = json.loads((ROOT / "data/replay/golden/status.json").read_text())
    original["T2-01"].update(status="pass", recorded_fail=False)
    original["T1-01"].update(status="recorded_fail", recorded_fail=True)
    status_path.write_text(json.dumps(original))
    before = status_path.read_bytes()
    run = tmp_path / "run"
    run.mkdir()
    (run / "observations.jsonl").write_text(
        observations()["T2-01"].model_dump_json() + "\n"
    )
    monkeypatch.setattr(regolden, "STATUS_PATH", status_path)
    regolden.main(["--run", str(run)])
    assert "T2-01" in capsys.readouterr().out
    assert status_path.read_bytes() == before
    regolden.main(["--run", str(run), "--write"])
    updated = json.loads(status_path.read_text())
    expected = json.loads(before)
    expected["T2-01"].update(status="recorded_fail", recorded_fail=True)
    assert (
        updated == expected
    )  # All other fields/tasks, including missing T1-01, remain.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["run", "status.json"]


def test_regolden_uses_manifest_rules_without_registration_guard(
    tmp_path, monkeypatch, capsys
):
    from govops.evalrun import regolden

    run = tmp_path / "run"
    run.mkdir()
    (run / "observations.jsonl").write_text(
        observations(RUN2)["T2-01"].model_dump_json() + "\n"
    )
    (run / "manifest.json").write_text(json.dumps({"grading_rules": "m3-grading"}))
    status = tmp_path / "status.json"
    status.write_bytes((ROOT / "data/replay/golden/status.json").read_bytes())
    before = status.read_bytes()
    monkeypatch.setattr(regolden, "STATUS_PATH", status)
    regolden.main(["--run", str(run)])
    output = capsys.readouterr().out
    assert output.rstrip().endswith("{}")
    assert "공식 규칙" in output and "m3-grading" in output
    assert "grading_rules 없음" not in output
    assert status.read_bytes() == before


def test_regolden_original_run_is_unchanged(capsys):
    from govops.evalrun import regolden

    before = regolden.STATUS_PATH.read_bytes()
    regolden.main(["--run", str(RUN3)])
    output = capsys.readouterr().out
    manifest = json.loads((RUN3 / "manifest.json").read_text())
    assert f"대상 실행의 공식 규칙으로 재계산: {manifest['grading_rules']}" in output
    assert "grading_rules 없음" not in output
    assert json.loads(output[output.index("{") :]) == {}
    assert regolden.STATUS_PATH.read_bytes() == before


def test_previous_official_run_differs_from_current_status_only_at_t3_01(capsys):
    from govops.evalrun import regolden

    before = regolden.STATUS_PATH.read_bytes()
    regolden.main(["--run", str(RUN2)])
    output = capsys.readouterr().out
    changes = json.loads(output[output.index("{") :])
    # 이전 공식 실행(두 번째 실행)은 보존한다. 현재 기록과 다른 것은 T3-01 하나뿐이다.
    assert set(changes) == {"T3-01"}
    assert changes["T3-01"]["before"]["status"] == "pass"
    assert changes["T3-01"]["after"]["status"] == "recorded_fail"
    assert regolden.STATUS_PATH.read_bytes() == before
