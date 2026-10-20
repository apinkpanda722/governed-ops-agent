"""Addendum 3: hydrated recomputation (no LLM) and the N2-2 positive locator."""

import json
import re
import stat
from pathlib import Path

import pytest
from test_grounding_cli import BODY, measure_ready, sheet_dir
from test_grounding_measurement_report import stopped_measurement

from govops.grounding import cli, measure_report
from govops.grounding import grader as grader_module
from govops.grounding.spend import STAGE_CAPS_USD

ROOT = Path(__file__).resolve().parents[1]


def test_locate_tasks_match_the_registered_list():
    text = (ROOT / "docs/results/hypotheses-m8-grader-addendum-3.md").read_text()
    section = text.split("## 2. 대상")[1].split("## 3.")[0]
    assert tuple(re.findall(r"`(T\d-\d\d)`", section)) == cli.LOCATE_TASKS
    assert len(cli.LOCATE_TASKS) == 18


def test_recompute_counts_hydrated_runs_by_their_evidence():
    result = {
        "runs": [
            {
                "position": "n2_1",
                "n_citations": 45,
                "n_fabricated": 4,
                "n_fabricated_strict": 0,
                "n_in_evidence_only": 4,
                "n_evidence_not_loaded": 0,
            }
        ]
    }
    hydrated = {
        "n2_2": {
            "n_citations": 49,
            "n_fabricated": 6,
            "n_fabricated_strict": 0,
            "n_in_evidence_only": 6,
            "n_evidence_not_loaded": 0,
        },
        "n2_3": {
            "n_citations": 47,
            "n_fabricated": 3,
            "n_fabricated_strict": 0,
            "n_in_evidence_only": 1,
            "n_evidence_not_loaded": 2,  # evidence status is not ok for these rows
        },
    }
    fixed = measure_report.recompute(result, [[], [], []], hydrated)
    assert fixed["source"] == "recomputed_with_evidence" and fixed["consistent"]
    assert [r["started"] for r in fixed["runs"]] == [True, False, False]
    assert [r.get("hydrated", False) for r in fixed["runs"]] == [False, True, True]
    assert fixed["n_fabricated"] == 13 and fixed["n_citations"] == 141
    assert fixed["n_in_evidence_only"] == 11 and fixed["n_evidence_not_loaded"] == 2
    assert fixed["fabricated_id_rate"] == pytest.approx(13 / 141)


def test_recompute_with_dbname_hydrates_unstarted_runs_without_any_call(
    tmp_path, monkeypatch, capsys
):
    run, args, out, _ = stopped_measurement(tmp_path, monkeypatch)
    before = (out / "measurement-result.json").read_bytes()
    spend_before = (out / "spend.jsonl").read_bytes()
    capsys.readouterr()
    assert run("recompute-measurement", *args) == 0  # the helper passes --dbname
    fixed = json.loads(capsys.readouterr().out)
    assert fixed["source"] == "recomputed_with_evidence" and fixed["consistent"]
    assert [r["started"] for r in fixed["runs"]] == [True, False, False]
    # The fake evidence loads fine (status ok) and holds none of the IDs: strict, not "unloaded".
    for unstarted in fixed["runs"][1:]:
        assert unstarted["hydrated"] is True
        assert unstarted["n_evidence_not_loaded"] == 0
        assert unstarted["n_fabricated_strict"] == unstarted["n_fabricated"] > 0
    assert fixed["n_evidence_not_loaded"] == 0
    assert fixed["fabricated_id_rate"] == pytest.approx(
        fixed["n_fabricated"] / fixed["n_citations"]
    )
    assert (out / "measurement-result.json").read_bytes() == before
    assert (out / "spend.jsonl").read_bytes() == spend_before  # LLM calls: 0
    assert (out / "measurement-recomputed.json").exists()
    # `report` carries the same hydrated recomputation.
    assert run("report", *args) == 0
    assert "recomputed_with_evidence" in capsys.readouterr().out


def locate_ready(tmp_path, monkeypatch, positives_per_row, *, expected_cost=0.03):
    """Validated environment, a measurement record with N2-2, and 3 locate rows."""
    run, paths = measure_ready(tmp_path, monkeypatch)
    out = sheet_dir(tmp_path).parent
    ids = sorted(
        json.loads(line)["task_id"]
        for line in (paths[1] / "observations.jsonl").read_text().splitlines()
    )[:3]
    monkeypatch.setattr(cli, "LOCATE_TASKS", tuple(ids))
    file = paths[1] / "observations.jsonl"
    rows = [json.loads(line) for line in file.read_text().splitlines()]
    for row in rows:
        row["answer"] = BODY + "."
        if row["task_id"] in ids:
            n = positives_per_row[ids.index(row["task_id"])]
            row["answer"] = (
                BODY + ".\n" + "\n".join(f"SYNTHETIC_POSITIVE{i}." for i in range(n))
            )
    file.write_text("".join(json.dumps(r) + "\n" for r in rows))
    (out / "measurement-result.json").write_text(
        json.dumps(
            {
                "runs": [
                    {"position": "n2_1", "cost_usd": 0.1},
                    {"position": "n2_2", "cost_usd": expected_cost * 3},
                ]
            }
        )
    )

    def cost(model, *, input_tokens, output_tokens, **kwargs):
        return 0.04 if output_tokens == grader_module.MAX_OUTPUT_TOKENS else 0.01

    monkeypatch.setattr(grader_module, "gemini_cost_usd", cost)
    return run, ["--results", *map(str, paths)], out, ids


def test_single_positive_is_located_with_ids_only(tmp_path, monkeypatch, capsys):
    run, args, out, ids = locate_ready(tmp_path, monkeypatch, [0, 1, 0])
    measurement = (out / "measurement-result.json").read_bytes()
    capsys.readouterr()
    assert run("locate-positives", *args) == 0
    printed = capsys.readouterr().out
    summary = json.loads(printed)
    assert summary["stage"] == "locate" and "공식 M6 수치 아님" in summary["notice"]
    assert summary["verdict"] == "single_positive"
    assert summary["human_check"] == [{"task_id": ids[1], "sentence_id": "s2"}]
    assert summary["n_graded_rows"] == 3 and summary["complete"] is True
    assert "SYNTHETIC_POSITIVE" not in printed and BODY not in printed
    detail = out / "locate-detail.jsonl"
    assert stat.S_IMODE(detail.stat().st_mode) == 0o600
    assert "SYNTHETIC_POSITIVE" not in detail.read_text()
    entry = json.loads(detail.read_text())
    assert entry["position"] == "n2_2" and entry["repeats"] == 1
    assert [row["task_id"] for row in entry["rows"]] == ids
    # Nothing is mixed into the measurement record.
    assert (out / "measurement-result.json").read_bytes() == measurement
    # The locate stage has its own spend and cap.
    assert 0 < json.loads(printed)["cost_usd"] <= 0.35
    # Running it again is refused.
    assert run("locate-positives", *args) == 2
    assert "FAIL already_located" in capsys.readouterr().out


@pytest.mark.parametrize("counts,n", [([0, 0, 0], 0), ([1, 2, 0], 3)])
def test_zero_or_many_positives_are_a_reproduction_mismatch(
    tmp_path, monkeypatch, capsys, counts, n
):
    run, args, _, _ = locate_ready(tmp_path, monkeypatch, counts)
    capsys.readouterr()
    assert run("locate-positives", *args) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["verdict"] == "reproduction_mismatch"
    assert summary["n_positive_sentences"] == n
    # With positives, every one of them goes to a human; with none there is nothing to pass on.
    assert len(summary["human_check"]) == n


def test_render_positives_falls_back_to_the_locate_record(
    tmp_path, monkeypatch, capsys
):
    run, args, out, ids = locate_ready(tmp_path, monkeypatch, [0, 1, 0])
    assert not (out / "measurement-detail.jsonl").exists()
    assert run("list-positives", *args) == 0
    assert "measurement_detail_not_recorded" in capsys.readouterr().out
    assert run("locate-positives", *args) == 0
    capsys.readouterr()
    assert run("list-positives", *args) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["source"] == "locate-detail.jsonl"
    assert [(p["task_id"], p["sentence_id"]) for p in listing["positives"]] == [
        (ids[1], "s2")
    ]
    assert run("render-positives", *args) == 0
    html = (out / "measure-positives/evidence.html").read_text()
    assert "확인 대상" in html and "SYNTHETIC_POSITIVE0." in html
    assert "<script" not in html
    mapping = json.loads((out / "measure-positives/positives.json").read_text())
    assert [m["task_id"] for m in mapping["rows"]] == [ids[1]]


def test_locate_refuses_a_task_list_mismatch_without_calls(
    tmp_path, monkeypatch, capsys
):
    run, args, out, ids = locate_ready(tmp_path, monkeypatch, [0, 1, 0])
    monkeypatch.setattr(cli, "LOCATE_TASKS", (ids[1], ids[0], ids[2]))
    capsys.readouterr()
    assert run("locate-positives", *args) == 2
    assert "FAIL measurement_order_mismatch" in capsys.readouterr().out
    assert not (out / "locate-detail.jsonl").exists()


def test_locate_cap_is_the_registered_addendum_4_value():
    assert STAGE_CAPS_USD["locate"] == 0.35


def test_locate_check_passes_when_expected_plus_reservation_fits(
    tmp_path, monkeypatch, capsys
):
    # Expected 0.30 + max reservation 0.04 = 0.34 <= 0.35 (it would not fit under 0.25).
    run, args, out, _ = locate_ready(
        tmp_path, monkeypatch, [0, 1, 0], expected_cost=0.30
    )
    capsys.readouterr()
    assert run("locate-positives", *args) == 0
    assert (out / "locate-detail.jsonl").exists()


def test_locate_stops_before_any_call_when_reservations_do_not_fit(
    tmp_path, monkeypatch, capsys
):
    # Expected 0.32 + max reservation 0.04 = 0.36 > the 0.35 stage cap.
    run, args, out, _ = locate_ready(
        tmp_path, monkeypatch, [0, 1, 0], expected_cost=0.32
    )
    capsys.readouterr()
    assert run("locate-positives", *args) == 2
    printed = capsys.readouterr().out
    assert "FAIL locate_reservation_margin" in printed
    # The computed values are shown so the margin can be checked.
    assert "expected_usd=0.3200" in printed and "max_reservation_usd=0.0400" in printed
    assert "cap_usd=0.35" in printed
    assert not (out / "locate-detail.jsonl").exists()
    spend = out / "spend.jsonl"
    assert '"stage":"locate"' not in (spend.read_text() if spend.exists() else "")


def test_locate_requires_the_measurement_record(tmp_path, monkeypatch, capsys):
    run, args, out, _ = locate_ready(tmp_path, monkeypatch, [0, 1, 0])
    (out / "measurement-result.json").unlink()
    capsys.readouterr()
    assert run("locate-positives", *args) == 2
    assert "FAIL measurement_result_missing" in capsys.readouterr().out


def test_missing_database_env_gives_the_fixed_code_not_a_hidden_failure(
    tmp_path, monkeypatch, capsys
):
    run, args, _, _ = stopped_measurement(tmp_path, monkeypatch)
    monkeypatch.delenv("SYNTHETIC_DB")
    capsys.readouterr()
    assert run("recompute-measurement", *args) == 2
    assert "FAIL url_env_unset" in capsys.readouterr().out
    monkeypatch.setenv("SYNTHETIC_DB", "  ")
    assert run("recompute-measurement", *args) == 2
    assert "FAIL url_env_unset" in capsys.readouterr().out


def test_missing_gemini_key_gives_the_fixed_code(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(cli.registration.RegistrationError, match="gemini_key_unset"):
        cli.build_grader(None, ledger_factory=object, spend_factory=object)
    monkeypatch.setenv("GEMINI_API_KEY", " ")
    with pytest.raises(cli.registration.RegistrationError, match="gemini_key_unset"):
        cli.build_grader(None, ledger_factory=object, spend_factory=object)
