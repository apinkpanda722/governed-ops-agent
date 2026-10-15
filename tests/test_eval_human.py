import csv
import json
import subprocess
from collections import Counter
from dataclasses import replace
from datetime import date

import pytest
from test_eval_observation import observation
from test_eval_tcr import spec

from govops import costs
from govops.eval import grading_cli, human
from govops.eval.grading import RubricScore, TokenUsage
from govops.eval.observation import dump_observations


@pytest.fixture(autouse=True)
def frozen_grader_date(monkeypatch):
    original = grading_cli.GeminiRubricGrader
    monkeypatch.setattr(
        grading_cli,
        "GeminiRubricGrader",
        lambda **kwargs: original(**kwargs, on=date(2026, 12, 31)),
    )


def synthetic():
    specs, observations = [], []
    for kind, n in human.HUMAN_SAMPLE_ALLOCATION.items():
        for i in range(n + 1):
            task = f"{kind}-{i:02}"
            specs.append(spec(task_id=task, type=kind, split="test"))
            observations.append(observation(task_id=task, answer=f"합성 답변 {i}"))
    return specs, observations


def filled(path):
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames, list(reader)
    for i, row in enumerate(rows):
        for dim in ["correctness", "groundedness", "format"]:
            row[dim] = str(i % 4)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def git(root, *args):
    subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, timeout=30
    )


def test_selection_sheet(tmp_path):
    specs, obs = synthetic()
    selected = human.select_human_sample(specs, obs)
    assert selected == human.select_human_sample(
        list(reversed(specs)), list(reversed(obs))
    )
    assert len(selected) == 30
    assert Counter(s.type for s, _ in selected) == human.HUMAN_SAMPLE_ALLOCATION
    assert [s.type for s, _ in selected] != sorted(s.type for s, _ in selected)
    assert human.sample_rule_sha256() == human.sample_rule_sha256()
    human.write_human_sheet(
        selected, tmp_path / "scores.csv", tmp_path / "human_sample.json"
    )
    with (tmp_path / "scores.csv").open() as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == [
            "sample_id",
            "task_id",
            "type",
            "request",
            "deliverable",
            "correctness",
            "groundedness",
            "format",
            "note",
        ]
        assert all(row["correctness"] == "" for row in reader)
    with pytest.raises(human.HumanGateError):
        human.register_human_grading(
            tmp_path / "scores.csv", tmp_path / "registration.json"
        )
    with pytest.raises(ValueError, match="T1"):
        human.select_human_sample(
            specs, [o for o in obs if not o.task_id.startswith("T1")]
        )


def test_dryrun_gate_and_reporting(tmp_path, monkeypatch):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Synthetic Reviewer")
    git(tmp_path, "config", "user.email", "synthetic@example.invalid")
    specs, obs = synthetic()
    csv_path = tmp_path / "data/eval/human/scores.csv"
    registration = tmp_path / "docs/results/human-grading-registration.json"
    human.write_human_sheet(
        human.select_human_sample(specs, obs),
        csv_path,
        csv_path.with_name("human_sample.json"),
    )
    filled(csv_path)
    human.register_human_grading(csv_path, registration)
    observations = tmp_path / "obs.jsonl"
    dump_observations(observations, obs)
    accesses = []

    async def call(prompt):
        accesses.append("call")
        return RubricScore(3, 3, 3), TokenUsage(10, 10)

    def factory():
        accesses.append("ledger")
        costs.init_ledgers(tmp_path / "ledger")
        return costs.CostLedger(tmp_path / "ledger")

    args = [
        "grade",
        "--observations",
        str(observations),
        "--out",
        str(tmp_path / "out"),
    ]

    def run():
        return grading_cli.main(
            args, repo_root=tmp_path, specs=specs, call=call, ledger_factory=factory
        )

    assert run() == 2 and accesses == []
    git(tmp_path, "add", str(csv_path), str(registration))
    # Staged but never committed is still rejected.
    assert run() == 2 and accesses == []
    git(tmp_path, "commit", "-m", "test: synthetic grading")
    assert run() == 0 and len(accesses) == 1 + len(obs) * 3
    report = json.loads((tmp_path / "out/report.json").read_text())
    for dim in ["correctness", "groundedness", "format"]:
        assert report["dimensions"][dim]["source"] == "human_sample_only"
        assert report["dimensions"][dim]["mean"] != 3
        assert report["appendix_gemini_only"][dim]["mean"] == 3
    assert (tmp_path / "out/kappa.json").exists()
    # A nearly exhausted temporary ledger allows one call, then blocks the next
    # reservation; the JSONL retains that observation's partial repeat results.
    from govops.eval.grading import GRADER_MODEL, MAX_OUTPUT_TOKENS, build_prompt

    first = min(obs, key=lambda o: (o.task_id, o.run_index))
    first_spec = next(s for s in specs if s.task_id == first.task_id)
    estimate = costs.gemini_cost_usd(
        GRADER_MODEL,
        input_tokens=2 * len(build_prompt(first_spec, first.answer).encode()) + 4096,
        output_tokens=MAX_OUTPUT_TOKENS,
        on=date(2026, 12, 31),
    )
    costs.init_ledgers(tmp_path / "limited-ledger")
    limited = costs.CostLedger(tmp_path / "limited-ledger")
    limited.record(
        purpose="grading",
        model=GRADER_MODEL,
        task_id="synthetic-old",
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.05 - estimate - 0.000001,
    )
    with monkeypatch.context() as patch:
        patch.setattr(costs, "SUPPLIER_CAPS_USD", {"gemini": 0.05})
        before_calls = accesses.count("call")
        assert (
            grading_cli.main(
                [
                    "grade",
                    "--observations",
                    str(observations),
                    "--out",
                    str(tmp_path / "out"),
                ],
                repo_root=tmp_path,
                specs=specs,
                call=call,
                ledger_factory=lambda: limited,
            )
            == 1
        )
        assert accesses.count("call") == before_calls + 1
    partial = json.loads((tmp_path / "out/grades.jsonl").read_text())
    assert len(partial["scores"]) == 1 and partial["error"] == "budget_or_price_block"
    assert not (tmp_path / "out/report.json").exists()
    before = len(accesses)
    original = csv_path.read_bytes()
    csv_path.write_bytes(original + b"\n")
    assert run() == 2 and len(accesses) == before
    csv_path.write_bytes(original)
    registration.write_text(registration.read_text() + "\n")
    assert run() == 2 and len(accesses) == before


def test_threshold_boundary():
    from govops.eval.kappa import (
        FALLBACK_LABEL,
        DimensionResult,
        KappaReport,
        final_dimension_source,
    )

    base = DimensionResult(0.59, (0.3, 0.8), False, 1, (0.8, 1.2), FALLBACK_LABEL)
    report = KappaReport(
        {"low": base, "ok": replace(base, kappa=0.6, trusted=True)}, 30, 0.6
    )
    assert final_dimension_source(report) == {
        "low": "human_sample_only",
        "ok": "gemini",
    }


@pytest.mark.parametrize("mode", ["pass", "score", "cost", "settlement"])
def test_smoke(tmp_path, mode, capsys, monkeypatch):
    costs.init_ledgers(tmp_path)
    target = costs.CostLedger(tmp_path)

    async def call(prompt):
        if mode == "score":
            return {"correctness": 4}, TokenUsage(10, 10)
        return RubricScore(1, 2, 3), TokenUsage(100000 if mode == "cost" else 10, 10)

    if mode == "settlement":
        monkeypatch.setattr(target, "settle", lambda *a, **kw: None)
    assert grading_cli.smoke(ledger=target, call=call) == (0 if mode == "pass" else 1)
    output = capsys.readouterr().out
    assert ("FAIL" not in output) == (mode == "pass")


def test_failure_codes_distinguish_call_and_response(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Synthetic Reviewer")
    git(tmp_path, "config", "user.email", "synthetic@example.invalid")
    specs, obs = synthetic()
    csv_path = tmp_path / "data/eval/human/scores.csv"
    registration = tmp_path / "docs/results/human-grading-registration.json"
    human.write_human_sheet(
        human.select_human_sample(specs, obs),
        csv_path,
        csv_path.with_name("human_sample.json"),
    )
    filled(csv_path)
    human.register_human_grading(csv_path, registration)
    git(tmp_path, "add", str(csv_path), str(registration))
    git(tmp_path, "commit", "-m", "test: synthetic grading")
    observations = tmp_path / "obs.jsonl"
    dump_observations(observations, obs)
    calls = []

    async def call(prompt):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("synthetic network failure")
        if len(calls) == 2:
            return "not a score", TokenUsage(10, 10)
        return RubricScore(3, 3, 3), TokenUsage(10, 10)

    def factory():
        costs.init_ledgers(tmp_path / "ledger")
        return costs.CostLedger(tmp_path / "ledger")

    status = grading_cli.main(
        ["grade", "--observations", str(observations), "--out", str(tmp_path / "out")],
        repo_root=tmp_path,
        specs=specs,
        call=call,
        ledger_factory=factory,
    )
    assert status == 1
    first = json.loads((tmp_path / "out/grades.jsonl").read_text().splitlines()[0])
    assert [r.get("error") for r in first["scores"]] == [
        "grading_call_failure",
        "grading_response_failure",
        None,
    ]
    assert first["median"] is None
    assert not (tmp_path / "out/report.json").exists()
