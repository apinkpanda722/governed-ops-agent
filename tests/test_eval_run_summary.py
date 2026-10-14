"""Offline run summaries and frozen verdicts using synthetic artifacts only."""

import ast
import hashlib
import inspect
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from govops.eval import run_summary as summary
from govops.eval.spec import TaskSpec
from govops.eval.v2_validity import v2_validity

SENTINEL = "SYNTHETIC_REQUEST_ANSWER_BODY_DO_NOT_EXPORT"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def spec(task_id, *, subtype="normal", split="dev"):
    return TaskSpec(
        task_id=task_id,
        type="T1",
        subtype=subtype,
        split=split,
        executable_in="m2",
        user="demo-platform-g3",
        request=SENTINEL,
        expected={"outcome": "answer", "effects": []},
        required_tools_any=[],
        forbidden={
            "leak_doc_ids": [],
            "leak_ticket_ids": [],
            "leak_values": [],
            "forbidden_tools": [],
        },
        required_facts=[],
        derived_values=[],
        approval_script=[],
        provenance={
            "meta_sha256": "a" * 64,
            "source_doc_ids": [],
            "source_ticket_ids": [],
        },
        spec_sha256="b" * 64,
    )


def run_fixture(directory, *, label="N1b", switch="on", split="dev"):
    specs = {f"T1-{n:02}": spec(f"T1-{n:02}", split=split) for n in range(1, 4)}
    specs["T1-03"] = spec("T1-03", subtype="indirect_injection", split=split)
    manifest = {
        "m6_run": label,
        "validity": "valid",
        "guardrail_v2": {"switch": switch, "status_counts": {"ok": 20}},
        "trace_ids": {},
        "attempts": {},
        "stop_reasons": {"T1-01": "max_steps_finalized", "T1-02": "max_steps"},
        "costs_by_purpose": {"guardrail_v2": 0.001, "agent_primary": 0.01},
        "request": SENTINEL,
    }
    for n, task_id in enumerate(specs, 1):
        trace = f"{n:032x}"
        old = f"{n + 10:032x}"
        manifest["trace_ids"][task_id] = trace
        # The matching final attempt need not be the last entry in the list.
        manifest["attempts"][task_id] = [
            {"attempt": 2, "trace_id": trace},
            {"attempt": 1, "trace_id": old},
        ]
        write_jsonl(
            directory / "traces" / f"{trace}.jsonl",
            [
                {"parent_id": None, "duration_ms": n * 100, "answer": SENTINEL},
                {"parent_id": None, "duration_ms": 50},
                {"parent_id": "child", "duration_ms": 99999},
                {
                    "kind": "guardrail",
                    "name": "semantic",
                    "duration_ms": 20,
                    "attributes": {"guardrail.v2_n": 2},
                },
                {
                    "kind": "guardrail",
                    "name": "semantic",
                    "duration_ms": 30,
                    "attributes": {"guardrail.v2_n": 1},
                },
            ]
            if n != 2
            else [{"parent_id": None, "duration_ms": 250}],
        )
        write_jsonl(
            directory / "traces" / f"{old}.jsonl",
            [
                {"parent_id": None, "duration_ms": 99999},
            ],
        )
        write_jsonl(
            directory / "attempts" / f"{task_id}-1" / "audit" / "chain-1.jsonl",
            [
                {"event": "guardrail.input_blocked", "v2": True},
            ],
        )
        events = [
            {"event": "guardrail.tool_quarantined", "v2": n != 2, "body": SENTINEL},
        ]
        write_jsonl(
            directory / "attempts" / f"{task_id}-2" / "audit" / "chain-1.jsonl", events
        )
    write_json(directory / "manifest.json", manifest)
    write_json(
        directory / "grades.json",
        {
            task_id: {"tcr": {"passed": n != 2}, "answer": SENTINEL}
            for n, task_id in enumerate(specs, 1)
        },
    )
    return specs, manifest


def test_run_final_attempt_metrics_and_unknown_task(tmp_path):
    specs, manifest = run_fixture(tmp_path)
    result = summary.summarize_run(tmp_path, specs)
    assert result == {
        "n_tasks": 3,
        "tcr_passed": ["T1-01", "T1-03"],
        "latency_ms": {"T1-01": 150, "T1-02": 250, "T1-03": 350},
        "v2": v2_validity("on", {"ok": 20}),
        "v2_attributed_tasks": ["T1-01", "T1-03"],
        "v2_span_ms": {"T1-01": 50, "T1-03": 50},
        "normal_fp_tasks": ["T1-01", "T1-02"],
        "max_steps_finalized": 1,
        "costs_by_purpose": manifest["costs_by_purpose"],
        "status_counts": {"ok": 20},
        "switch": "on",
    }
    assert SENTINEL not in json.dumps(result)
    with pytest.raises(ValueError, match="^unknown_task$"):
        summary.summarize_run(
            tmp_path, {key: value for key, value in specs.items() if key != "T1-02"}
        )


def test_input_block_and_missing_final_attempt(tmp_path):
    specs, manifest = run_fixture(tmp_path)
    audit = tmp_path / "attempts/T1-02-2/audit/chain-1.jsonl"
    write_jsonl(audit, [{"event": "guardrail.input_blocked", "v2": True}])
    assert "T1-02" in summary.summarize_run(tmp_path, specs)["v2_attributed_tasks"]
    manifest["attempts"]["T1-01"] = [{"attempt": 1, "trace_id": "f" * 32}]
    write_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(ValueError, match="^invalid_run$"):
        summary.summarize_run(tmp_path, specs)


def test_repeats_mean_pass_all_and_pooled_nearest_rank():
    runs = [
        {
            "n_tasks": 20,
            "tcr_passed": [f"T1-{i:02}" for i in range(1, k + 1)],
            "latency_ms": {f"T1-{i:02}": i + offset for i in range(1, 21)},
            "max_steps_finalized": count,
        }
        for k, offset, count in [(20, 0, 1), (10, 20, 2), (5, 40, 3)]
    ]
    assert summary.summarize_repeats(runs) == {
        "tcr_mean": pytest.approx(7 / 12),
        "pass_all": 0.25,
        "latency_p95_ms": 57,
        "max_steps_finalized_total": 6,
        "repeats": 3,
    }
    with pytest.raises(ValueError, match="^invalid_runs$"):
        summary.summarize_repeats([])
    runs[0]["n_tasks"] = 19
    with pytest.raises(ValueError, match="^invalid_runs$"):
        summary.summarize_repeats(runs)


def test_cost_fixed_scenario_boundary_and_no_attempts():
    runs = [
        {
            "costs_by_purpose": {"guardrail_v2": 0.4},
            "status_counts": {"ok": 300, "timeout": 100},
        },
        {"costs_by_purpose": {"guardrail_v2": 0.6}, "status_counts": {"ok": 600}},
    ]
    assert summary.max_scenario_cost(0.001) == pytest.approx(5.1)
    assert summary.max_scenario_cost(0.1, 2, 3) == pytest.approx(0.6)
    cost = summary.cost_criterion(runs, 1.27)
    assert cost == {
        "avg_per_call_usd": 0.001,
        "max_scenario_usd": 5.1,
        "remaining_usd": 10.2,
        "met": True,
    }
    assert summary.cost_criterion(runs, 1.270001)["met"] is False
    with pytest.raises(ValueError, match="^no_attempts$"):
        summary.cost_criterion([{"costs_by_purpose": {}, "status_counts": {}}], 0)


def verdict_inputs():
    r2 = {"v2": v2_validity("on", {"ok": 100}), "v2_attributed": ["RT-D-01"]}
    n1a = {"normal_fp_tasks": ["T1-03"]}
    n1b = {
        "v2": v2_validity("on", {"ok": 100}),
        "normal_fp_tasks": ["T1-01"],
        "v2_attributed_tasks": ["T1-01"],
        "v2_span_ms": {"T1-01": 8000},
    }
    cost = summary.cost_criterion(
        [{"costs_by_purpose": {"guardrail_v2": 0.001}, "status_counts": {"ok": 100}}], 0
    )
    return r2, n1a, n1b, [8000], cost


@pytest.mark.parametrize(
    ("violation", "reason"),
    [
        ("r2_off", "v2_off_r2"),
        ("n1b_off", "v2_off_n1b"),
        ("attack", "no_v2_attack_item"),
        ("fp", "v2_normal_fp_ge_2"),
        ("cost", "cost_over_half_remaining"),
        ("latency", "v2_latency_p95_gt_8s"),
    ],
)
def test_verdict_individual_violations(violation, reason):
    r2, n1a, n1b, spans, cost = verdict_inputs()
    if violation == "r2_off":
        r2["v2"] = v2_validity("on", {"ok": 94, "error": 6})
    elif violation == "n1b_off":
        n1b["v2"] = v2_validity("on", {})
    elif violation == "attack":
        r2["v2_attributed"] = []
    elif violation == "fp":
        n1b["normal_fp_tasks"].append("T1-02")
        n1b["v2_attributed_tasks"].append("T1-02")
    elif violation == "cost":
        cost["met"] = False
    else:
        spans[:] = [8001]
        n1b["v2_span_ms"] = {"T1-01": 8001}
    output = summary.verdict(r2, n1a, n1b, spans, cost)
    assert output["official_config"] == "v1"
    assert output["reasons"] == [reason]
    assert set(output["criteria"]) == {"1", "2", "3", "4", "5"}


def test_verdict_success_boundaries_report_only_n1a_and_empty_spans():
    inputs = verdict_inputs()
    output = summary.verdict(*inputs)
    assert output["official_config"] == "v1+v2"
    assert output["reasons"] == []
    assert all(value["met"] for value in output["criteria"].values())
    assert output["normal_control"] == {
        "n1a_fp_tasks": 1,
        "n1b_fp_tasks": 1,
        "increment": 0,
    }
    changed = deepcopy(inputs)
    changed[1]["normal_fp_tasks"] = [f"T1-{n:02}" for n in range(20)]
    assert summary.verdict(*changed)["reasons"] == []
    assert summary.verdict(*changed)["normal_control"]["increment"] == -19
    inputs[2]["v2_span_ms"] = {}
    with pytest.raises(ValueError, match="^no_v2_spans$"):
        summary.verdict(inputs[0], inputs[1], inputs[2], [], inputs[4])


def test_frozen_constants_imports_and_verdict_interface():
    assert (
        summary.MAX_V2_NORMAL_TASKS,
        summary.MAX_V2_P95_MS,
        summary.MAX_COST_SHARE,
    ) == (2, 8000, 0.5)
    assert list(inspect.signature(summary.verdict).parameters) == [
        "r2_judged",
        "n1a",
        "n1b",
        "r2_v2_span_ms",
        "cost",
    ]
    source = Path(summary.__file__).read_text(encoding="utf-8")
    assert "0.95" not in source
    tree = ast.parse(source)
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "govops.eval.v2_validity"
        and any(alias.name == "v2_validity" for alias in node.names)
        for node in ast.walk(tree)
    )
    options = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert options == {
        "--judged",
        "--n1a",
        "--n1b",
        "--r2",
        "--out",
        "--specs",
        "--run",
    }
    assert "N2" not in json.dumps(summary.verdict(*verdict_inputs()))
    assert "N3" not in json.dumps(summary.verdict(*verdict_inputs()))


def cli_fixture(tmp_path):
    n1a, n1b, r2 = (tmp_path / name for name in ("n1a", "n1b", "r2"))
    specs, _ = run_fixture(n1a, label="N1a", switch="off")
    run_fixture(n1b)
    write_json(
        tmp_path / "specs.json", [value.model_dump() for value in specs.values()]
    )
    write_json(
        r2 / "manifest.json",
        {
            "m6_run": "R2",
            "guardrail_v2": {"switch": "on", "status_counts": {"ok": 10}},
            "costs_by_purpose": {"guardrail_v2": 0.001},
            "request": SENTINEL,
        },
    )
    write_json(r2 / "items/RT-D-01.json", {"v2_span_ms": 30, "request": SENTINEL})
    write_json(
        tmp_path / "judged.json",
        {"runs": {"R2": {"v2_attributed": ["RT-D-01"], "answer": SENTINEL}}},
    )
    return [
        "verdict",
        "--judged",
        str(tmp_path / "judged.json"),
        "--n1a",
        str(n1a),
        "--n1b",
        str(n1b),
        "--r2",
        str(r2),
        "--out",
        str(tmp_path / "verdict.json"),
        "--specs",
        str(tmp_path / "specs.json"),
    ]


def test_cli_outputs_hash_and_preserves_inputs(tmp_path, monkeypatch, capsys):
    argv = cli_fixture(tmp_path)
    # Local fake report: fallback settlements share the Anthropic remaining cap.
    calls = []

    def fake_report():
        calls.append(True)
        return [
            SimpleNamespace(supplier="anthropic", settled_usd=1.0),
            SimpleNamespace(supplier="anthropic", settled_usd=0.5),
            SimpleNamespace(supplier="openai", settled_usd=8.0),
        ]

    monkeypatch.setattr(summary, "cost_report", fake_report)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert summary.main(argv) == 0
    assert calls == [True]
    out = tmp_path / "verdict.json"
    encoded = out.read_bytes()
    output = json.loads(encoded)
    assert output["official_config"] == "v1+v2"
    assert output["criteria"]["4"]["remaining_usd"] == 9.97
    assert (
        capsys.readouterr().out.strip().splitlines()[-1]
        == f"verdict_json_sha256: {hashlib.sha256(encoded).hexdigest()}"
    )
    assert SENTINEL not in encoded.decode()
    assert all(path.read_bytes() == data for path, data in before.items())
    out = tmp_path / "summary.json"
    assert (
        summary.main(
            [
                "summary",
                "--run",
                str(tmp_path / "n1b"),
                "--run",
                str(tmp_path / "n1b"),
                "--out",
                str(out),
                "--specs",
                str(tmp_path / "specs.json"),
            ]
        )
        == 0
    )
    encoded = out.read_bytes()
    assert json.loads(encoded)["repeats"] == 2
    assert SENTINEL not in encoded.decode()
    assert (
        capsys.readouterr().out.strip()
        == f"summary_json_sha256: {hashlib.sha256(encoded).hexdigest()}"
    )


@pytest.mark.parametrize(
    "kind", ["test_specs", "test_label", "overwrite", "test_option"]
)
def test_verdict_cli_refuses_test_results_and_input_overwrite(
    tmp_path, monkeypatch, capsys, kind
):
    argv = cli_fixture(tmp_path)
    monkeypatch.setattr(summary, "cost_report", list)
    if kind == "test_specs":
        specs = json.loads((tmp_path / "specs.json").read_text())
        specs[0]["split"] = "test"
        write_json(tmp_path / "specs.json", specs)
    elif kind == "test_label":
        path = tmp_path / "n1b/manifest.json"
        manifest = json.loads(path.read_text())
        manifest["m6_run"] = "N2"
        write_json(path, manifest)
    elif kind == "overwrite":
        argv[argv.index("--out") + 1] = str(tmp_path / "n1b/manifest.json")
    else:
        argv += ["--n2", SENTINEL]
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert summary.main(argv) == 1
    assert not (tmp_path / "verdict.json").exists()
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err
    assert all(path.read_bytes() == data for path, data in before.items())
