"""Measurement aggregation, offline re-aggregation and the positive-sentence tools."""

import json
import stat

import pytest
from test_eval_observation import observation
from test_eval_tcr import spec
from test_grounding_agreement import ANSWER
from test_grounding_cli import measure_ready, sheet_dir

from govops.grounding import cli, measure_report, sample
from govops.grounding import grader as grader_module
from govops.grounding.evidence import Evidence, EvidenceItem
from govops.grounding.spend import STAGE_CAPS_USD, M8Spend


def pool_row(*, evidence, observed=("syn-guide-001",)):
    obs = observation(task_id="T1-01", answer=ANSWER, observed_ids=list(observed))
    return sample.PoolRow(spec(task_id="T1-01", split="dev"), obs, evidence)


def ok(text):
    return Evidence("ok", (EvidenceItem("lookup_id", text, len(text)),), len(text), 0)


def test_rates_split_loaded_unloaded_and_keep_the_registered_rate():
    # ANSWER cites syn-guide-001 (observed), INC-2026-004 and SYN-1003 (not observed).
    present = pool_row(evidence=ok("본문 INC-2026-004 만 있다"))
    unloaded = pool_row(evidence=None)
    oversize = pool_row(evidence=Evidence("evidence_oversize", (), 0, 0))
    rates = cli._rates(sample.SampleRows([present, unloaded, oversize]), [], 1)
    assert rates["n_citations"] == 9 and rates["n_fabricated"] == 6
    assert rates["n_in_evidence_only"] == 1  # INC-2026-004 appears in the evidence
    assert rates["n_fabricated_strict"] == 1  # SYN-1003 is nowhere in the evidence
    assert rates["n_evidence_not_loaded"] == 4  # two rows whose evidence was not loaded
    assert rates["n_fabricated"] == (
        rates["n_fabricated_strict"]
        + rates["n_in_evidence_only"]
        + rates["n_evidence_not_loaded"]
    )
    # The registered rate still covers every row it was given.
    assert rates["fabricated_id_rate"] == pytest.approx(6 / 9)
    assert rates["evidence_not_loaded_rate"] == pytest.approx(4 / 9)


def test_unloaded_rows_are_never_strict():
    # The reported defect: rows without evidence made every non-observed ID "strict".
    rates = cli._rates(sample.SampleRows([pool_row(evidence=None)]), [], 1)
    assert rates["n_fabricated"] == 2
    assert rates["n_fabricated_strict"] == 0 and rates["n_in_evidence_only"] == 0
    assert rates["n_evidence_not_loaded"] == 2


def stopped_measurement(tmp_path, monkeypatch):
    """A measurement that hits the stage cap during the first run, like the real one."""
    run, paths = measure_ready(tmp_path, monkeypatch)
    for path in paths:
        file = path / "observations.jsonl"
        rows = [json.loads(line) for line in file.read_text().splitlines()]
        for row in rows:
            row["answer"] = ANSWER  # cites syn-guide-001, INC-2026-004, SYN-1003
            row["observed_ids"] = ["syn-guide-001"]
        file.write_text("".join(json.dumps(r) + "\n" for r in rows))
    spend = M8Spend(sheet_dir(tmp_path).parent / "spend.jsonl")
    spend.settle(spend.reserve("measurement", 0), STAGE_CAPS_USD["measurement"] - 0.1)

    def cost(model, *, input_tokens, output_tokens, **kwargs):
        # Reservation estimates (max output) 0.04, confirmed usage 0.01 per call.
        return 0.04 if output_tokens == grader_module.MAX_OUTPUT_TOKENS else 0.01

    monkeypatch.setattr(grader_module, "gemini_cost_usd", cost)
    args = ["--results", *map(str, paths)]
    assert run("measure", *args) == 1  # stop_code makes the exit non-zero
    out = sheet_dir(tmp_path).parent
    return run, args, out, json.loads((out / "measurement-result.json").read_text())


def test_stopped_measurement_aggregates_by_what_was_loaded(tmp_path, monkeypatch):
    _, _, _, result = stopped_measurement(tmp_path, monkeypatch)
    assert result["status"] == "stopped"
    # A spend-cap stop is named as such (it used to read budget_or_price_block).
    assert result["stop_code"] == "m8_stage_cap_exceeded"
    assert len(result["runs"]) == 1 and 0 < result["n_graded_rows"] < 36
    first = result["runs"][0]
    assert first["position"] == "n2_1" and first["run_id"] == "test-run"
    # Rows of the runs that never started read no evidence: not strict, not in-evidence.
    assert first["n_evidence_not_loaded"] == 0 and first["n_fabricated_strict"] > 0
    assert result["n_fabricated"] == (
        result["n_fabricated_strict"]
        + result["n_in_evidence_only"]
        + result["n_evidence_not_loaded"]
    )
    # Strict counts only the rows whose evidence was loaded (the first run).
    assert result["n_fabricated_strict"] == first["n_fabricated_strict"]
    assert result["n_in_evidence_only"] == first["n_in_evidence_only"] == 0
    assert result["n_evidence_not_loaded"] == 2 * first["n_fabricated"]
    assert result["fabricated_id_rate"] == pytest.approx(
        result["n_fabricated"] / result["n_citations"]
    )


def test_offline_recompute_fixes_an_old_result_without_any_call(
    tmp_path, monkeypatch, capsys
):
    run, args, out, result = stopped_measurement(tmp_path, monkeypatch)
    # Turn the file into the old shape: the buggy top level and no not-loaded counts.
    old = dict(result)
    first = dict(old["runs"][0])
    first.pop("n_evidence_not_loaded", None)
    old["runs"] = [first]
    old["n_fabricated_strict"] = old["n_fabricated"]
    old["n_in_evidence_only"] = 0
    old.pop("n_evidence_not_loaded", None)
    (out / "measurement-result.json").write_text(json.dumps(old))
    before = (out / "measurement-result.json").read_bytes()
    capsys.readouterr()
    assert run("recompute-measurement", *args, dbname=None) == 0
    fixed = json.loads(capsys.readouterr().out)
    assert fixed["consistent"] is True and fixed["source"] == "recomputed_offline"
    assert [r["started"] for r in fixed["runs"]] == [True, False, False]
    assert fixed["n_fabricated_strict"] == first["n_fabricated_strict"]
    assert fixed["n_in_evidence_only"] == first["n_in_evidence_only"]
    unstarted = fixed["runs"][1]["n_evidence_not_loaded"]
    assert unstarted > 0 and fixed["runs"][2]["n_evidence_not_loaded"] == unstarted
    assert fixed["n_fabricated"] == (
        fixed["n_fabricated_strict"] + fixed["n_in_evidence_only"] + 2 * unstarted
    )
    assert fixed["fabricated_id_rate"] == pytest.approx(
        fixed["n_fabricated"] / fixed["n_citations"]
    )
    assert fixed["stored_top_level"]["n_fabricated_strict"] == old["n_fabricated"]
    # The original file is untouched; the recomputation is written next to it.
    assert (out / "measurement-result.json").read_bytes() == before
    assert (out / "measurement-recomputed.json").exists()
    assert stat.S_IMODE((out / "measurement-recomputed.json").stat().st_mode) == 0o600
    # `report` carries the recomputation too.
    assert run("report", *args, dbname=None) == 0
    assert '"measurement_recomputed"' in capsys.readouterr().out


def test_recompute_requires_a_result_and_reports_a_fixed_code(
    tmp_path, monkeypatch, capsys
):
    run, paths = measure_ready(tmp_path, monkeypatch)
    capsys.readouterr()
    assert run("recompute-measurement", "--results", *map(str, paths)) == 2
    assert "FAIL measurement_result_missing" in capsys.readouterr().out


def test_positive_list_and_cards_use_recorded_rows(tmp_path, monkeypatch, capsys):
    run, args, out, _ = stopped_measurement(tmp_path, monkeypatch)
    capsys.readouterr()
    assert run("list-positives", *args) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["n_positive_sentences"] > 0
    item = listing["positives"][0]
    assert set(item) == {
        "run_id",
        "position",
        "task_id",
        "sentence_id",
        "repeat_labels",
        "majority_positive",
    }
    assert item["position"] == "n2_1" and len(item["repeat_labels"]) == 3
    assert {"unsupported", "contradicted"} & set(item["repeat_labels"])
    printed = json.dumps(listing, ensure_ascii=False)
    assert "합성 보고" not in printed  # IDs and labels only
    assert run("render-positives", *args) == 0
    target = out / "measure-positives"
    html = (target / "evidence.html").read_text()
    assert "확인 대상" in html and "<script" not in html
    assert stat.S_IMODE((target / "evidence.html").stat().st_mode) == 0o600
    mapping = json.loads((target / "positives.json").read_text())["rows"]
    assert {m["run_id"] for m in mapping} == {item["run_id"]}
    assert "합성" not in capsys.readouterr().out.replace("합성 근거", "")


def test_without_recorded_rows_the_tools_say_so(tmp_path, monkeypatch, capsys):
    run, args, out, _ = stopped_measurement(tmp_path, monkeypatch)
    (out / "measurement-detail.jsonl").unlink()
    capsys.readouterr()
    assert run("list-positives", *args) == 0
    text = capsys.readouterr().out
    assert "measurement_detail_not_recorded" in text and "per_run_counts" in text
    assert run("render-positives", *args) == 2
    assert "FAIL measurement_detail_not_recorded" in capsys.readouterr().out
    assert not (out / "measure-positives").exists()


def test_positives_function_lists_any_repeat_with_majority_flag(tmp_path):
    path = tmp_path / "detail.jsonl"
    path.write_text(
        json.dumps(
            {
                "run_id": "run-a",
                "position": "n2_2",
                "rows": [
                    {
                        "task_id": "T1-01",
                        "repeat_labels": [
                            {"s1": "supported", "s2": "unsupported"},
                            {"s1": "supported", "s2": "supported"},
                            {"s1": "supported", "s2": "supported"},
                        ],
                        "positive_majority": [],
                    },
                    {
                        "task_id": "T1-02",
                        "repeat_labels": [{"s10": "contradicted"}] * 3,
                        "positive_majority": ["s10"],
                    },
                ],
            }
        )
        + "\n"
    )
    found = measure_report.positives(path, None)
    assert (
        found["n_positive_sentences"] == 2
        and found["n_positive_sentences_majority"] == 1
    )
    assert [
        (i["task_id"], i["sentence_id"], i["majority_positive"])
        for i in found["positives"]
    ] == [
        ("T1-01", "s2", False),
        ("T1-02", "s10", True),
    ]
    assert found["positives"][0]["repeat_labels"] == [
        "unsupported",
        "supported",
        "supported",
    ]
