"""읽기 전용 비용 비율과 파일럿 재추정."""

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

from govops.costs import REPORT_PLAN, CostLedger, cost_report, format_report, main


def settlement(cost=0.002, *, purpose="agent_primary", task="task-a", rid=None):
    row = {
        "ts": "2026-10-04T00:00:00+00:00",
        "purpose": purpose,
        "supplier": {"agent_primary": "openai", "grading": "gemini"}[purpose],
        "model": "synthetic-model",
        "task_id": task,
        "input_tokens": 10,
        "cached_input_tokens": 0,
        "output_tokens": 2,
        "cost_usd": cost,
    }
    if rid is not None:
        row["reservation_id"] = rid
    return row


def reservation(rid, estimate=0.010, **kwargs):
    row = settlement(rid=rid, **kwargs)
    for key in ("cost_usd", "input_tokens", "cached_input_tokens", "output_tokens"):
        del row[key]
    return dict(row, kind="reservation", estimate_usd=estimate)


def write(root, rows=(), grading=()):
    for name, items in (("costs.jsonl", rows), ("costs_grading.jsonl", grading)):
        (root / name).write_text("".join(json.dumps(r) + "\n" for r in items))


def test_ratios_exclusions_tasks_and_totals(tmp_path):
    write(
        tmp_path,
        [
            reservation("a"),
            settlement(rid="a"),
            reservation("b"),
            settlement(0.012, rid="b"),
            settlement(0.003, task="task-b"),
            reservation("pending"),
            reservation("zero", 0, task=None),
            settlement(0, rid="zero", task=None),
            settlement(0.001, rid="missing", task=None),
        ],
        [reservation("g", purpose="grading"), settlement(rid="g", purpose="grading")],
    )
    reports = cost_report(tmp_path)
    primary = reports[0]
    assert primary.ratio.n == 2
    assert primary.ratio.mean == pytest.approx(0.7)
    assert (primary.ratio.p50, primary.ratio.p95, primary.ratio.max) == (0.2, 1.2, 1.2)
    assert primary.ratio.over_one == 1
    assert primary.legacy_rows == primary.unsettled_reservations == 1
    assert primary.zero_estimates == primary.unmatched_settlements == 1
    assert primary.missing_task_rows == 2
    assert primary.tasks == 2
    assert primary.mean_usd_per_task == pytest.approx(0.017 / 2)
    assert primary.settled_calls == 5
    ledger = CostLedger(tmp_path)
    for supplier in ("openai", "anthropic", "gemini"):
        assert sum(
            r.settled_usd for r in reports if r.supplier == supplier
        ) == pytest.approx(ledger.total_usd(supplier))
    output = format_report(reports, plan={"sample": 10})
    for phrase in (
        "파일럿 전 참고용",
        "정산 호출 5",
        "추정 표본 정산 호출 3, task 2",
        "task 2",
        "$0.085000",
        "0.93%",
        "데이터 없음",
        "경고",
        "추정액 0",
        "보수적으로",
        "task_id 없음",
        "행 수 합계: 11",
        str(tmp_path),
    ):
        assert phrase in output


@pytest.mark.parametrize(
    "ratios,p50,p95",
    [([], None, None), ([0.3], 0.3, 0.3), (list(range(1, 21)), 10, 19)],
)
def test_nearest_rank(tmp_path, ratios, p50, p95):
    rows = []
    for i, ratio in enumerate(ratios):
        rows.extend([reservation(str(i), 1), settlement(ratio, rid=str(i))])
    write(tmp_path, rows)
    stats = cost_report(tmp_path)[0].ratio
    assert (stats.p50, stats.p95) == (p50, p95)
    assert stats.n == len(ratios)
    if not ratios:
        assert stats.mean is stats.max is None


def test_plan_and_conservative_remaining(tmp_path):
    write(tmp_path, [reservation("pending", 1), settlement(0.01)])
    output = format_report(cost_report(tmp_path))
    assert "합계 560" in output
    assert "$5.600000" in output
    assert "61.34%" in output
    assert "$8.120000" in output
    assert "68.97%" in output
    assert "장애 주입 표본은 미정이므로 제외" in output


def test_m6_report_plan_has_six_keys_and_560_task_runs():
    assert len(REPORT_PLAN) == 6
    assert sum(REPORT_PLAN.values()) == 560


def test_real_ledger_copy_is_read_only(tmp_path):
    source = Path(__file__).resolve().parents[1] / "data/manifest"
    before = [
        (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).digest())
        for p in (source / "costs.jsonl", source / "costs_grading.jsonl")
    ]
    for name in ("costs.jsonl", "costs_grading.jsonl"):
        shutil.copyfile(source / name, tmp_path / name)
    copied = {
        p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in tmp_path.iterdir()
    }
    output = format_report(cost_report(tmp_path))
    assert not re.search(r"sk-|Bearer|Authorization|synthetic-model|444c8bff", output)
    assert copied == {
        p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in tmp_path.iterdir()
    }
    assert before == [
        (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).digest())
        for p in (source / "costs.jsonl", source / "costs_grading.jsonl")
    ]


def test_cli_errors_and_success(tmp_path, capsys):
    assert main(["report", "--root", str(tmp_path)]) == 1
    assert "Cost ledger missing" in capsys.readouterr().err
    write(tmp_path)
    assert main(["report", "--root", str(tmp_path)]) == 0
    assert "파일럿 전 참고용" in capsys.readouterr().out
    (tmp_path / "costs.jsonl").write_text('{"secret": "sk-never-print"}\n')
    assert main(["report", "--root", str(tmp_path)]) == 1
    error = capsys.readouterr().err
    assert "Invalid cost ledger line 1" in error
    assert "sk-never-print" not in error


def test_mismatched_purpose_is_counted(tmp_path):
    write(tmp_path, [reservation("a")], [settlement(rid="a", purpose="grading")])
    reports = cost_report(tmp_path)
    assert reports[0].unsettled_reservations == 1
    assert reports[3].unmatched_settlements == 1
    assert reports[3].ratio.n == 0
