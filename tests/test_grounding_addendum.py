"""Addendum 1 behaviour with synthetic rows: injections, stops, gates, reports, v3.1."""

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from test_eval_observation import observation
from test_eval_tcr import spec
from test_grounding_agreement import ANSWER, Grader, human_for
from test_grounding_cli import environment, label, sheet_dir
from test_grounding_registration import commit

from govops.eval.grading import GradingError
from govops.grounding import cli, metrics, prompt, sample
from govops.grounding.evidence import Evidence, EvidenceItem
from govops.grounding.grader import GradeResult, SentenceResult
from govops.grounding.spend import SpendCapError

ORIGINAL_IDS = {"syn-guide-001", "INC-2026-004", "SYN-1003"}
ORIGINAL_PAIRS = {("s1", "syn-guide-001"), ("s2", "INC-2026-004"), ("s3", "SYN-1003")}
BUNDLE = json.dumps(
    {
        "documents": [
            {"doc_id": "syn-guide-001", "text": "합성 보고"},
            {"doc_id": "INC-2026-004", "text": "확인 기록"},
            {"key": "SYN-1003", "text": "조치 기록"},
            {"doc_id": "syn-guide-009", "text": "무관"},
        ]
    },
    ensure_ascii=False,
)


def bundle_rows(
    answer=ANSWER,
    observed=("syn-guide-001", "INC-2026-004", "SYN-1003", "syn-guide-009"),
):
    obs = observation(task_id="T1-01", answer=answer, observed_ids=list(observed))
    row = sample.PoolRow(
        spec(task_id="T1-01", split="dev"),
        obs,
        Evidence(
            "ok", (EvidenceItem("lookup_id", BUNDLE, len(BUNDLE)),), len(BUNDLE), 0
        ),
    )
    return sample.SampleRows([row])


class Detecting(Grader):
    """Flags every sentence number and (sentence, citation) pair not in the original."""

    def __init__(self):
        super().__init__(lambda sid, doc: doc in ORIGINAL_IDS)
        self.calls = []

    async def grade(self, *, sentences, **kwargs):
        self.calls.append([(s.sentence_id, s.citations) for s in sentences])
        rows = [
            SentenceResult(
                s.sentence_id,
                "supported" if s.sentence_id in {"s1", "s2", "s3"} else "unsupported",
                s.citations,
                ("E1",),
                tuple(
                    (doc, (s.sentence_id, doc) in ORIGINAL_PAIRS) for doc in s.citations
                ),
            )
            for s in sentences
        ]
        return GradeResult(tuple(rows))


def human_all_supported(rows):
    return human_for(rows, lambda sid, doc: True)


def test_every_kind_is_applied_after_all_rows_in_kind_order():
    rows = bundle_rows()
    human, supports = human_all_supported(rows)
    grader = Detecting()
    sink = []
    result = asyncio.run(
        cli._evaluate(
            rows,
            human,
            supports,
            grader=grader,
            version="v3.0",
            stage="calibration",
            repeats=1,
            detail_sink=sink,
        )
    )
    assert len(grader.calls) == 4  # the row, then fact, fabricated_id, wrong_support
    assert [sid for sid, _ in grader.calls[0]] == ["s1", "s2", "s3"]
    assert [sid for sid, _ in grader.calls[1]][-1] == "s4"  # fact: new number
    fabricated = [doc for _, docs in grader.calls[2] for doc in docs]
    assert any(doc.startswith("syn-injected-") for doc in fabricated)
    # wrong_support swaps one citation for a different observed ID of the same row.
    assert [docs for _, docs in grader.calls[3]] != [
        docs for _, docs in grader.calls[0]
    ]
    assert all(
        doc in ORIGINAL_IDS | {"syn-guide-009"}
        for _, docs in grader.calls[3]
        for doc in docs
    )
    detection = result["detection"]
    assert (
        detection["denominators"]["injected_rows"] == 3 and detection["detected"] == 3
    )
    assert set(detection["by_kind"]) == set(cli.inject.INJECTION_KINDS)
    for stats in detection["by_kind"].values():
        assert stats["n"] == stats["detected"] == 1 and stats["ci"] is not None
    assert [e["assigned"] for e in sink if e["kind"] == "injection"] == [
        "fact",
        "fabricated_id",
        "wrong_support",
    ]
    assert cli.INJECTION_ORDER == ("fact", "fabricated_id", "wrong_support")
    assert set(cli.INJECTION_ORDER) == set(cli.inject.INJECTION_KINDS)
    assert result["row_failure_ratio"] == 0.0 and result["n_failed_rows"] == 0


def test_stage_cap_during_injections_keeps_every_graded_row():
    rows = bundle_rows()
    human, supports = human_all_supported(rows)

    class CapAtInjection(Detecting):
        async def grade(self, **kwargs):
            if self.calls:
                raise SpendCapError("synthetic")
            return await super().grade(**kwargs)

    sink = []
    result = asyncio.run(
        cli._evaluate(
            rows,
            human,
            supports,
            grader=CapAtInjection(),
            version="v3.0",
            stage="calibration",
            repeats=1,
            detail_sink=sink,
        )
    )
    assert (
        result["stop_code"] == "m8_stage_cap_exceeded" and result["status"] == "stopped"
    )
    assert result["n_ungraded_rows"] == 0 and result["n_rows"] == 1
    assert result["detection"]["denominators"]["injected_rows"] == 0
    assert [e["outcome"] for e in sink if e["kind"] == "injection"] == ["stopped"]


def test_failed_row_counts_in_the_failure_ratio_and_gets_no_injection():
    rows = bundle_rows()
    human, supports = human_all_supported(rows)

    class Failing(Detecting):
        async def grade(self, **kwargs):
            raise GradingError("grading_call_failure")

    result = asyncio.run(
        cli._evaluate(
            rows,
            human,
            supports,
            grader=Failing(),
            version="v3.0",
            stage="calibration",
            repeats=1,
        )
    )
    assert result["n_failed_rows"] == 1 and result["row_failure_ratio"] == 1.0
    assert result["failures"] == {"grading_call_failure": 1}
    assert result["detection"]["skip_count"] == 0


def test_skip_causes_are_recorded_per_row_type():
    rows = bundle_rows(answer="합성 보고입니다.\n확인했습니다.")
    human, supports = (
        {"T1-01": {"s1": "supported", "s2": "supported"}},
        {"T1-01": {}},
    )
    sink = []
    result = asyncio.run(
        cli._evaluate(
            rows,
            human,
            supports,
            grader=Detecting(),
            version="v3.0",
            stage="calibration",
            repeats=1,
            detail_sink=sink,
        )
    )
    skips = {e["assigned"]: e for e in sink if e["kind"] == "injection"}
    assert skips["fabricated_id"]["cause"] == "no_cited_sentence"
    assert skips["wrong_support"]["cause"] == "no_cited_sentence"
    assert skips["fact"]["outcome"] == "graded" and skips["fact"]["type"] == "T1"
    assert result["detection"]["skip_causes"] == {"no_cited_sentence": 2}


@pytest.mark.parametrize(
    "validation,allowed",
    [
        ({"verdict": "채점기 v3 검증 통과", "complete": True}, True),
        ({"verdict": "채점기 v3 검증 통과", "complete": False}, False),
        ({"verdict": "채점기 v3 검증 미달", "complete": True}, False),
        ({"verdict": "판정 불가(양성 부족)", "complete": True}, False),
        (
            {
                "verdict": metrics.LIMITED_VERDICT,
                "original_verdict": "판정 불가(양성 부족)",
                "stop_code": None,
                "status": "complete",
                "complete": False,
            },
            True,
        ),
        (
            {
                "verdict": metrics.LIMITED_VERDICT,
                "original_verdict": "판정 불가(양성 부족)",
                "stop_code": "m8_stage_cap_exceeded",
                "status": "stopped",
            },
            False,
        ),
        (
            {
                "verdict": metrics.LIMITED_VERDICT,
                "original_verdict": "판정 불가(양성 부족)",
                "stop_code": None,
                "status": "stopped",
            },
            False,
        ),
        (
            {
                "verdict": metrics.LIMITED_VERDICT,
                "stop_code": None,
                "status": "complete",
            },
            False,
        ),
        (
            {
                "verdict": metrics.LIMITED_VERDICT,
                "original_verdict": "채점기 v3 검증 미달",
                "stop_code": None,
                "status": "complete",
            },
            False,
        ),
    ],
)
def test_measurement_gate_for_full_and_limited_validation(validation, allowed):
    assert cli.measurement_allowed(validation) is allowed


def test_a_stopped_v30_does_not_block_v31_but_an_unfinished_run_does():
    stopped = {
        "version": "v3.0",
        "status": "stopped",
        "stop_code": "m8_stage_cap_exceeded",
        "kappa": None,
    }
    cli.check_revision("v3.1", [stopped])
    with pytest.raises(ValueError, match="calibration_incomplete"):
        cli.check_revision("v3.1", [{"version": "v3.0", "status": "started"}])
    with pytest.raises(ValueError, match="revision_order"):
        cli.check_revision("v3.2", [stopped])
    # A stopped record is not an adoption candidate.
    complete = {
        "version": "v3.1",
        "status": "complete",
        "kappa": 0.2,
        "detection_rate": 0.5,
    }
    assert cli.choose_adopted([stopped, complete])["version"] == "v3.1"


def test_citation_metrics_split_strict_and_in_evidence_only():
    obs = observation(task_id="T1-01", answer=ANSWER, observed_ids=["syn-guide-001"])
    text = "본문에 INC-2026-004 만 있다"
    row = sample.PoolRow(
        spec(task_id="T1-01", split="dev"),
        obs,
        Evidence("ok", (EvidenceItem("lookup_id", text, len(text)),), len(text), 0),
    )
    rates = cli._rates(sample.SampleRows([row]), [], 1)
    assert rates["n_citations"] == 3 and rates["n_fabricated"] == 2
    assert rates["n_fabricated_strict"] == 1 and rates["n_in_evidence_only"] == 1
    assert rates["fabricated_id_rate"] == pytest.approx(2 / 3)
    assert rates["fabricated_strict_rate"] == pytest.approx(1 / 3)
    assert rates["in_evidence_only_rate"] == pytest.approx(1 / 3)


def test_report_human_rates_cover_calibration_heldout_and_combined(tmp_path):
    def write(split, labels):
        directory = tmp_path / split
        directory.mkdir()
        lines = ["sample_id,sentence_id,task_id,type,request,sentence,label,note"]
        for sample_id, sid, value in labels:
            lines.append(f"{sample_id},{sid},T,T1,,x,{value},")
        (directory / "labels.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    write(
        "calibration",
        [
            ("S01", "s1", "supported"),
            ("S01", "s2", "unsupported"),
            ("S02", "s1", "not_claim"),
        ],
    )
    assert cli._human_rates(tmp_path)["heldout"] == {"unavailable": "labels_missing"}
    write("heldout", [("S01", "s1", "supported"), ("S02", "s1", "")])
    partial = cli._human_rates(tmp_path)
    assert partial["heldout"] == {"unavailable": "labels_incomplete"}
    assert partial["calibration"]["sentence_rate"] == 0.5
    assert "M6 층화 표본" in partial["caveat"]
    (tmp_path / "heldout/labels.csv").write_text(
        "sample_id,sentence_id,task_id,type,request,sentence,label,note\n"
        "S01,s1,T,T1,,x,supported,\nS02,s1,T,T1,,x,contradicted,\n",
        encoding="utf-8",
    )
    full = cli._human_rates(tmp_path)
    assert full["combined"]["n_positive_sentences"] == 2
    assert full["combined"]["n_rows"] == 4  # rows are namespaced by split
    assert full["combined"]["sentence_ci"] == metrics.wilson(2, 4)


def test_v31_prompt_file_in_the_repository_passes_the_work_tree_gates(
    tmp_path, monkeypatch
):
    run, calls = environment(tmp_path, monkeypatch)
    assert run("sheet-calibration") == 0
    label(tmp_path, "calibration")
    assert run("register-labels") == 0
    commit(tmp_path)
    assert run("calibrate") == 0
    source = Path(__file__).resolve().parents[1] / "docs/m8-prompts/v3_1.txt"
    target = tmp_path / "docs/m8-prompts/v3_1.txt"
    target.parent.mkdir(parents=True)
    shutil.copy(source, target)
    commit(tmp_path)  # a committed prompt file leaves the gated scope clean
    before = len(calls)
    assert run("calibrate", "--version", "v3.1", "--prompt-file", str(target)) == 0
    assert len(calls) > before
    log = [
        json.loads(line)
        for line in (sheet_dir(tmp_path).parent / "calibration-log.jsonl")
        .read_text()
        .splitlines()
    ]
    done = [e for e in log if e.get("status") == "complete"]
    assert [e["version"] for e in done] == ["v3.0", "v3.1"]
    with cli._template("v3.1", target):
        assert done[1]["prompt_sha256"] == prompt.prompt_sha256("v3.1")
    assert done[0]["prompt_sha256"] != done[1]["prompt_sha256"]
    # The per-row detail file accumulates from the first run on.
    detail = (
        (sheet_dir(tmp_path).parent / "calibration-detail.jsonl")
        .read_text()
        .splitlines()
    )
    assert [json.loads(d)["version"] for d in detail] == ["v3.0", "v3.1"]
    # Without the file the unregistered version cannot run.
    assert run("calibrate", "--version", "v3.2") != 0


def test_v30_prompt_file_is_unchanged():
    path = Path(__file__).resolve().parents[1] / "src/govops/grounding/prompts/v3_0.txt"
    assert prompt.prompt_sha256("v3.0").startswith("c37b72a7")
    assert path.exists()
