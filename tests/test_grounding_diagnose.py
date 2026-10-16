"""Offline diagnostics over synthetic run artifacts; output holds IDs, counts and codes."""

import csv
import json

from test_eval_observation import observation
from test_grounding_registration import commit, repository

from govops.eval.observation import dump_observations
from govops.grounding import cards, cli, diagnose
from govops.grounding.evidence import EvidenceItem

PRIVATE_TEXT = "SYNTHETIC_SENTENCE_BODY"
SENTENCES = {
    ("S01", "s1"): f"{PRIVATE_TEXT} 하나(syn-guide-001).",
    ("S01", "s2"): f"{PRIVATE_TEXT} 둘(INC-2026-004).",
    ("S02", "s1"): f"{PRIVATE_TEXT} 셋(SYN-1003).",
}
TASKS = {"S01": "T1-01", "S02": "T1-02"}


def write_csv(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def build(root, *, detail=True):
    out = root / "data/interim/m8-grounding"
    sheet = out / "calibration"
    sheet.mkdir(parents=True)
    labels = [
        {
            "sample_id": sample,
            "sentence_id": sid,
            "task_id": TASKS[sample],
            "type": "T1" if sample == "S01" else "T2",
            "request": "요청 INC-2026-004" if sid == "s1" and sample == "S01" else "",
            "sentence": text,
            "label": {"s1": "supported", "s2": "unsupported"}[sid],
            "note": "",
        }
        for (sample, sid), text in SENTENCES.items()
    ]
    labels[2]["label"] = "supported"
    write_csv(sheet / "labels.csv", list(labels[0]), labels)
    citations = [
        {
            "sample_id": "S01",
            "sentence_id": "s1",
            "doc_id": "syn-guide-001",
            "supports": "지지",
        },
        {
            "sample_id": "S01",
            "sentence_id": "s2",
            "doc_id": "INC-2026-004",
            "supports": "지지",
        },
        {
            "sample_id": "S02",
            "sentence_id": "s1",
            "doc_id": "SYN-1003",
            "supports": "비지지",
        },
    ]
    write_csv(sheet / "citations.csv", list(citations[0]), citations)
    (sheet / "evidence.html").write_text(
        cards.render_cards(
            labels,
            citations,
            {"S01": "r", "S02": "r"},
            {
                "S01": [EvidenceItem("lookup_id", "본문 SYN-1003 포함", 10)],
                "S02": [EvidenceItem("lookup_id", "무관", 2)],
            },
        ),
        encoding="utf-8",
    )
    entry = {
        "version": "v3.0",
        "kappa": None,
        "status": "stopped",
        "stop_code": "m8_stage_cap_exceeded",
        "complete": False,
        "failures": {"grading_call_failure": 1},
        "detection": {"skip_reasons": {"no_candidate": 1}},
    }
    (out / "calibration-log.jsonl").write_text(
        json.dumps({"version": "v3.0", "status": "started"})
        + "\n"
        + json.dumps(entry)
        + "\n"
    )
    results = root / "results"
    results.mkdir()
    dump_observations(
        results / "observations.jsonl",
        [
            observation(task_id="T1-01", observed_ids=["syn-guide-001"]),
            observation(task_id="T1-02", observed_ids=[]),
        ],
    )
    ledger = root / "data/manifest"
    ledger.mkdir(parents=True)
    rows = []

    def call(task, second, estimate, cost, start, end, tokens=(100, 50)):
        rid = f"r{len(rows)}"
        key = diagnose._hash(f"{task}:0")
        rows.append(
            {
                "ts": start,
                "kind": "reservation",
                "reservation_id": rid,
                "purpose": "grading",
                "supplier": "gemini",
                "model": "m",
                "task_id": f"m8:calibration:{key}",
                "estimate_usd": estimate,
            }
        )
        rows.append(
            {
                "ts": end,
                "purpose": "grading",
                "supplier": "gemini",
                "model": "m",
                "task_id": f"m8:calibration:{key}",
                "input_tokens": tokens[0],
                "cached_input_tokens": 0,
                "output_tokens": tokens[1],
                "cost_usd": cost,
                "reservation_id": rid,
            }
        )

    call(
        "T1-01",
        False,
        0.05,
        0.01,
        "2026-10-16T14:00:00+00:00",
        "2026-10-16T14:00:10+00:00",
    )
    call(
        "T1-01",
        True,
        0.05,
        0.02,
        "2026-10-16T14:00:10+00:00",
        "2026-10-16T14:00:20+00:00",
    )
    call(
        "T1-02",
        False,
        0.10,
        0.10,
        "2026-10-16T14:00:20+00:00",
        "2026-10-16T14:01:20+00:00",
        (9000, 8192),
    )
    probe = {
        "ts": "2026-10-16T13:00:00+00:00",
        "kind": "reservation",
        "reservation_id": "p",
        "purpose": "grading",
        "supplier": "gemini",
        "model": "m",
        "task_id": "m8:calibration:cost-probe-0",
        "estimate_usd": 0.04,
    }
    (ledger / "costs_grading.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in [probe, *rows])
    )
    if detail:
        detail_rows = [
            {
                "kind": "row",
                "task_id": "T1-01",
                "type": "T1",
                "status": "graded",
                "cost_usd": 0.03,
                "labels": {"s1": "supported", "s2": "supported"},
                "supports": [
                    ["s1", "syn-guide-001", True],
                    ["s2", "INC-2026-004", False],
                ],
            },
            {
                "kind": "row",
                "task_id": "T1-02",
                "type": "T2",
                "status": "failed",
                "code": "grading_call_failure",
                "cost_usd": 0.1,
            },
            {
                "kind": "injection",
                "task_id": "T1-01",
                "type": "T1",
                "assigned": "fact",
                "outcome": "skipped",
                "code": "no_candidate",
            },
        ]
        (out / "calibration-detail.jsonl").write_text(
            json.dumps({"version": "v3.0", "rows": detail_rows}) + "\n"
        )
    return out


def test_sections_from_ledger_log_sheets_and_card(tmp_path):
    build(tmp_path, detail=False)
    result = diagnose.diagnose(
        tmp_path, version="v3.0", test_results=tmp_path / "results"
    )
    assert result["log"]["stop_code"] == "m8_stage_cap_exceeded"
    cost = result["cost"]
    assert cost["n_calls"] == 3 and cost["total_usd"] == 0.13
    assert cost["charged_at_reservation"]["n_calls"] == 1
    assert cost["charged_at_reservation"]["samples"] == ["S02"]
    assert cost["charged_at_reservation"]["seconds"]["max"] == 60.0
    assert cost["charged_at_reservation"]["share_of_total"] == round(0.10 / 0.13, 4)
    assert cost["cost_per_call_usd"] == {"min": 0.01, "median": 0.02, "max": 0.1}
    assert [c["cumulative_usd"] for c in cost["curve"]] == [0.01, 0.03, 0.13]
    assert result["ledger_rows"]["second_call_rows"] == ["S01"]
    assert result["ledger_rows"]["no_second_call_by_type"] == {"T2": 1}
    assert result["human"]["label_counts"] == {"supported": 2, "unsupported": 1}
    assert result["human"]["positive_sentences"] == {"unsupported": ["S01:s2"]}
    fab = result["fabricated"]
    ids = {(i["sample_id"], i["id"]): i for i in fab["items"]}
    # INC-2026-004 appears in the request, so only SYN-1003 is fabricated.
    assert set(ids) == {("S02", "SYN-1003")}
    assert ids[("S02", "SYN-1003")]["id_type"] == "ticket"
    assert ids[("S02", "SYN-1003")]["in_evidence_text"] is False
    assert result["detail"] == {"unavailable": "detail_not_recorded"}
    by_type = result["human"]["citation_labels_by_type_and_in_evidence"]
    assert by_type["지지|document|in_evidence=False"] == 1


def test_detail_tables_and_codes(tmp_path):
    build(tmp_path)
    detail = diagnose.diagnose(
        tmp_path, version="v3.0", test_results=tmp_path / "results"
    )["detail"]
    assert detail["disagreements"] == [
        {
            "sample_id": "S01",
            "sentence_id": "s2",
            "human": "unsupported",
            "grader": "supported",
        }
    ]
    assert detail["citation_table"] == {
        "지지|true": 1,
        "지지|false": 1,
        "비지지|ungraded_row": 1,
    }
    assert detail["row_status"]["S02"] == {
        "status": "failed",
        "code": "grading_call_failure",
    }
    assert detail["injection_skips"] == {"no_candidate|T1": 1}
    assert detail["n_graded_rows"] == 1


def test_output_contains_no_sentence_text_and_cli_is_offline(tmp_path, capsys):
    repository(tmp_path)
    build(tmp_path)
    commit(tmp_path)
    code = cli.main(
        ["diagnose-calibration", "--test-results", str(tmp_path / "results")],
        repo_root=tmp_path,
    )
    printed = capsys.readouterr().out
    assert code == 0
    assert PRIVATE_TEXT not in printed and "요청" not in printed
    assert json.loads(printed)["scope"] == "m8"


def test_missing_inputs_are_reported_as_codes(tmp_path):
    assert diagnose.diagnose(tmp_path)["human"] == {"unavailable": "sheets_missing"}
    out = build(tmp_path, detail=False)
    (tmp_path / "data/manifest/costs_grading.jsonl").unlink()
    (out / "calibration/evidence.html").unlink()
    result = diagnose.diagnose(tmp_path, test_results=tmp_path / "results")
    assert result["cost"] == {"unavailable": "ledger_missing"}
    assert result["fabricated"] == {"unavailable": "evidence_card_missing"}


def test_legacy_single_kind_attribution_is_reconstructed_from_sheets_and_ledger(
    tmp_path,
):
    build(tmp_path, detail=False)
    result = diagnose.diagnose(
        tmp_path, version="v3.0", test_results=tmp_path / "results"
    )
    cells = result["injection_attribution_legacy_single_kind"]
    # S01 holds the human-positive sentence, so only S02 (T2) was a candidate.
    assert sum(c["assigned"] for c in cells.values()) == 1
    ((name, cell),) = cells.items()
    assert name.endswith("|T2") and cell == {
        "assigned": 1,
        "second_call": 0,
        "without_second_call": 1,
    }
