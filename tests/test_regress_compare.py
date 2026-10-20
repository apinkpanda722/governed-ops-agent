"""Offline regression contract, using synthetic dev-20 artifacts exclusively."""

import ast
import hashlib
import inspect
import json
import re
import subprocess
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from test_eval_tcr import spec as base_spec

from govops.eval.observation import load_observations
from govops.golden import GOLDEN_TASKS, digest_for, golden_expectations
from govops.metrics import cli as metrics_cli
from govops.metrics.toolcall import evaluate_run, summarize
from govops.regress import compare as regression
from govops.regress.golden_structure import expectation_mismatches, golden_diff

TASKS = tuple(
    sorted((*GOLDEN_TASKS, "T2-02", "T2-03", "T2-09", "T3-02", "T3-08", "T4-06"))
)
SENTINELS = ("SYN_ANSWER_PRIVATE", "SYN_EFFECT_PRIVATE", "SYN_TOOL_RESULT_PRIVATE")
REGISTRATION = "docs/results/hypotheses-m8-regression.md"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def synthetic_specs():
    result = {}
    for task in TASKS:
        write = task.startswith(("T2", "T3", "T4", "T6"))
        effect = {
            "kind": "slack_message",
            "target": "team-platform",
            "count": 1,
            "structure": {},
        }
        result[task] = base_spec(
            task_id=task,
            type=task[:2],
            executable_in="m3" if write else "m2",
            expected={
                "outcome": "approved_executed" if write else "answer",
                "effects": [effect] if write else [],
            },
            required_tools_any=[["lookup_id"]],
        )
    return result


def build_synthetic_run(root: Path, *, name: str, **overrides) -> Path:
    """N1a-shaped dev 20; overrides: role, manifest, observations, grades, spans.

    Mapping overrides replace rows by task ID; spans replace that task's trace.
    No real evaluation files or services are consulted.
    """
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "attempts").mkdir()
    role = overrides.pop("role", "control")
    expected = regression.EXPECTED[role]
    specs = synthetic_specs()
    trace_ids = {task: f"{i:032x}" for i, task in enumerate(TASKS, 1)}
    manifest = {
        "validity": "valid",
        "infrastructure_failures": 0,
        "unmapped_models": [],
        "models_seen": [expected.model],
        "mixed_model_tasks": {"tasks": [], "count": 0},
        "fallback_used": expected.fallback_used,
        "models_by_task": {task: [expected.model] for task in TASKS},
        "config": "v1",
        "guardrail_v2": {
            "switch": "off",
            "cfg_sha256": "a" * 64,
            "status_counts": {},
            "runner_concurrency": 1,
        },
        "prompt_hash": "b" * 64,
        "contract_snapshot_sha256": "c" * 64,
        "guardrail": {
            "rules_sha256": "d" * 64,
            "hook_digests_seen": [{"rules": "d" * 12, "code": "e" * 12}],
        },
        "a_git_head": "f" * 40,
        "trace_ids": trace_ids,
        "costs_by_purpose": {
            "agent_primary": 0.00786116,
            "agent_fallback": 0.0,
            "guardrail_v2": 0.0,
            "grading": 0.0,
        },
        "run_label": "N1a" if role == "control" else "M8-CAND",
        "run_role": "reference",
    }
    manifest.update(overrides.pop("manifest", {}))
    obs_updates = overrides.pop("observations", {})
    grade_updates = overrides.pop("grades", {})
    span_updates = overrides.pop("spans", {})
    if overrides:
        raise TypeError(f"unknown synthetic overrides: {sorted(overrides)}")
    observations, grades = [], {}
    # 52 tool calls, all ok. Two root task spans on approval tasks.
    for i, task in enumerate(TASKS):
        write = specs[task].executable_in == "m3"
        calls = [
            {"name": "lookup_id", "ok": True},
            {"name": "get_document", "ok": True},
        ]
        if i < 12:
            calls.append(
                {"name": "slack_send_message" if write else "search_docs", "ok": True}
            )
        obs = {
            "task_id": task,
            "run_index": 0,
            "status": "completed",
            "answer": SENTINELS[0],
            "observed_ids": [],
            "cited_doc_ids": [],
            "tool_calls": calls,
            "tool_results": [],
            "effects": [
                dict(e.model_dump(), approval_id="approval", body_sha256=None)
                for e in specs[task].expected.effects
            ],
            "effect_bodies": [SENTINELS[1]] if write else [],
            "approvals": [
                {
                    "approval_id": "approval",
                    "actor": "requester",
                    "decision": "approve",
                    "http_status": 200,
                }
            ]
            if write
            else [],
            "policy_violations": [],
            "fault_injected": False,
            "trace_id": trace_ids[task],
            "stop_reason": None,
        }
        obs.update(obs_updates.get(task, {}))
        observations.append(obs)
        passed = task not in {"T3-01", "T3-02"}
        grade = {
            "tcr": {
                "task_id": task,
                "run_index": 0,
                "passed": passed,
                "reasons": []
                if passed
                else ["effect_structure_mismatch", "invalid_status"],
                "tool_coverage": True,
            },
            "rules": {"unapproved_effect_count": int(task == "T3-01")},
            "used_auto_forbidden": False,
        }
        grade.update(grade_updates.get(task, {}))
        grades[task] = grade
        latency = 17070.0 if i == 19 else 13832.05 if i == 18 else 10000.0
        roots = [
            {
                "kind": "task",
                "name": "task",
                "parent_id": None,
                "duration_ms": latency / (2 if write else 1),
                "output": {"answer": SENTINELS[0]},
            }
            for _ in range(2 if write else 1)
        ]
        spans = roots + [
            {
                "kind": "model_call",
                "name": "agent-primary",
                "parent_id": "root",
                "duration_ms": 50,
                "attributes": {
                    "deployment_id": expected.deployment_id,
                    "model": expected.model,
                    "model_group": "agent-primary",
                    "fallback_used": expected.fallback_used,
                    "unmapped_model": False,
                    "attempted_fallbacks": 0,
                    "cost_usd": 0.0001,
                    "usage": {},
                    "error_code": None,
                },
            },
            {
                "kind": "tool_call",
                "name": "lookup_id",
                "parent_id": "root",
                "duration_ms": 1,
                "attributes": {"tool.result": SENTINELS[2]},
            },
        ]
        write_jsonl(
            directory / "traces" / f"{trace_ids[task]}.jsonl",
            span_updates.get(task, spans),
        )
    write_jsonl(directory / "observations.jsonl", observations)
    write_json(directory / "grades.json", grades)
    write_json(directory / "manifest.json", manifest)
    return directory


@pytest.fixture
def pair(tmp_path):
    b = regression.load_run(build_synthetic_run(tmp_path / "pair", name="baseline"))
    c = regression.load_run(build_synthetic_run(tmp_path / "pair", name="candidate"))
    return b, c, synthetic_specs()


def verdict(pair, *, role="control"):
    b, c, specs = pair
    details = regression.build_details(b, c, specs, role=role)
    return regression.reduce_details(details, b, c, role=role)


def test_real_shape_and_pass(pair, tmp_path):
    b, _c, specs = pair
    rows = load_observations(b.directory / "observations.jsonl")
    assert len(rows) == 20 and len(rows[0].model_dump()) == 15
    assert all(row.run_index == 0 for row in rows)
    assert sum(len(row.tool_calls) for row in rows) == 52
    assert "repeat_index" not in b.manifest
    v = verdict(pair)
    assert v["verdict"] == "PASS" and v["reasons"] == []
    assert v["criteria"]["2"]["values"]["baseline_passed"] == 18
    assert v["criteria"]["6"]["values"]["baseline_p95_ms"] == 13832.05
    candidate = regression.load_run(
        build_synthetic_run(tmp_path, name="sonnet", role="candidate")
    )
    assert verdict((b, candidate, specs), role="candidate")["verdict"] == "PASS"
    assert verdict((b, candidate, specs))["reasons"] == [
        "invalid_run",
        "invalid_model_mismatch",
        "invalid_fallback_flag",
        "invalid_deployment_evidence",
    ]


@pytest.mark.parametrize(
    "code,change",
    [
        ("invalid_validity", {"validity": "invalid"}),
        ("invalid_infrastructure_failure", {"infrastructure_failures": 1}),
        ("invalid_unmapped_model", {"unmapped_models": ["unknown"]}),
        ("invalid_model_mismatch", {"models_seen": ["other"]}),
        (
            "invalid_mixed_model",
            {"mixed_model_tasks": {"tasks": [TASKS[0]], "count": 1}},
        ),
        ("invalid_fallback_flag", {"fallback_used": True}),
        ("invalid_environment_changed", {"prompt_hash": "e" * 64}),
        ("invalid_config", {"config": "v1+v2"}),
        (
            "invalid_status_counts",
            {"guardrail_v2": {"switch": "off", "status_counts": {"ok": 1}}},
        ),
    ],
)
def test_independent_validity_codes(pair, code, change):
    b, c, specs = pair
    c = replace(c, manifest=c.manifest | change)
    v = verdict((b, c, specs))
    assert v["reasons"] == ["invalid_run", code]


def test_deployment_all_spans_and_task_set(pair):
    b, c, specs = pair
    bad = dict(c.model_calls[0], deployment_id="other")
    assert verdict((b, replace(c, model_calls=[*c.model_calls, bad]), specs))[
        "reasons"
    ] == ["invalid_run", "invalid_deployment_evidence"]
    details = regression.build_details(b, c, specs, role="control")
    duplicate = replace(c, observations=[*c.observations, c.observations[0]])
    assert regression.reduce_details(details, b, duplicate, role="control")[
        "reasons"
    ] == ["invalid_run", "invalid_task_set"]
    all_bad = replace(
        c, manifest=c.manifest | {"validity": "bad", "infrastructure_failures": 1}
    )
    assert verdict((b, all_bad, specs))["criteria"]["1"]["codes"] == [
        "invalid_run",
        "invalid_validity",
        "invalid_infrastructure_failure",
    ]


def details_for(pair):
    return regression.build_details(*pair, role="control")


@pytest.mark.parametrize("drop,met", [(2, True), (3, False)])
def test_tcr_boundary_and_detail_is_only_input(pair, drop, met):
    b, c, _ = pair
    rows = details_for(pair)
    passing = [i for i, row in enumerate(rows) if row.candidate.passed]
    for i in passing[:drop]:
        rows[i] = replace(rows[i], candidate=replace(rows[i].candidate, passed=False))
    v = regression.reduce_details(rows, b, c, role="control")
    assert v["criteria"]["2"]["met"] is met
    assert v["reasons"] == ([] if met else ["tcr_drop"])
    # Improvement offsets one loss: the registered metric is net count difference.
    i = next(i for i, row in enumerate(rows) if not row.baseline.passed)
    rows[i] = replace(rows[i], candidate=replace(rows[i].candidate, passed=True))
    assert regression.reduce_details(rows, b, c, role="control")["verdict"] == "PASS"


@pytest.mark.parametrize("reason", regression.FORBIDDEN_REASONS)
def test_forbidden_reasons_only(pair, reason):
    b, c, _ = pair
    rows = details_for(pair)
    rows[0] = replace(
        rows[0], candidate=replace(rows[0].candidate, forbidden_reasons=[reason])
    )
    v = regression.reduce_details(rows, b, c, role="control")
    assert v["reasons"] == ["forbidden_violation"]
    assert verdict(pair)["criteria"]["3"]["met"]  # rules count=1 is not a TCR reason.


@pytest.mark.parametrize(
    "metric,loss,expected",
    [
        ("required", 2, []),
        ("required", 3, ["toolcall_required_drop"]),
        ("ok", 2, []),
        ("ok", 3, ["toolcall_ok_drop"]),
    ],
)
def test_toolcall_boundaries(pair, metric, loss, expected):
    b, c, _ = pair
    rows = details_for(pair)
    # Exact denominators 20 required tasks and 40 calls, independent of other criteria.
    rows = [
        replace(
            r,
            baseline=replace(
                r.baseline, reads_total=1, reads_ok=1, writes_total=1, writes_ok=1
            ),
            candidate=replace(
                r.candidate, reads_total=1, reads_ok=1, writes_total=1, writes_ok=1
            ),
        )
        for r in rows
    ]
    for i in range(loss):
        update = {"required_met": False} if metric == "required" else {"reads_ok": 0}
        rows[i] = replace(rows[i], candidate=replace(rows[i].candidate, **update))
    assert regression.reduce_details(rows, b, c, role="control")["reasons"] == expected


@pytest.mark.parametrize(
    "amount,expected", [(0.01179174, []), (0.01179175, ["cost_over"])]
)
def test_cost_boundary_includes_fallback(pair, amount, expected):
    b, c, specs = pair
    c = replace(
        c,
        manifest=c.manifest
        | {
            "costs_by_purpose": {
                "agent_primary": 0,
                "agent_fallback": amount,
                "guardrail_v2": 0,
                "grading": 900,
            }
        },
    )
    v = verdict((b, c, specs))
    assert v["reasons"] == expected
    assert v["behavior_criteria_met"] and v["cost_criterion_met"] is (not expected)


@pytest.mark.parametrize("latency,expected", [(20000, []), (20001, ["latency_over"])])
def test_latency_boundary(pair, latency, expected):
    b, c, _ = pair
    rows = [
        replace(r, candidate=replace(r.candidate, latency_ms=latency))
        for r in details_for(pair)
    ]
    assert regression.reduce_details(rows, b, c, role="control")["reasons"] == expected


def test_golden_structure_and_report_only(pair):
    b, c, specs = pair

    def changed(task, **updates):
        observations = [
            o.model_copy(update=updates) if o.task_id == task else o
            for o in c.observations
        ]
        return replace(c, observations=observations)

    failing = changed("T3-01", effects=[])
    v = verdict((b, failing, specs))
    assert v["verdict"] == "PASS"
    assert (
        "effect_count"
        in v["criteria"]["5"]["values"]["baseline_failing_golden"]["T3-01"]
    )
    assert verdict((b, changed("T3-07", effects=[]), specs))["reasons"] == [
        "golden_structure"
    ]
    report = changed(
        "T1-01",
        tool_calls=list(reversed(c.observations[0].tool_calls)),
        answer="찾을 수 없습니다",
    )
    v = verdict((b, report, specs))
    assert v["verdict"] == "PASS"
    assert v["criteria"]["5"]["values"]["report_only"]["T1-01"] == [
        "tool_order",
        "refused",
    ]
    # Same effect count, different m3 target: effects must also be compared.
    obs = next(o for o in c.observations if o.task_id == "T3-07")
    different = [obs.effects[0].model_copy(update={"target": "team-other"})]
    assert verdict((b, changed("T3-07", effects=different), specs))["reasons"] == [
        "golden_structure"
    ]
    for index in range(3):
        mappings = [
            specs,
            {o.task_id: o for o in b.observations},
            {o.task_id: o for o in c.observations},
        ]
        mappings[index] = {k: v for k, v in mappings[index].items() if k != "T1-01"}
        with pytest.raises(ValueError, match="^m8_regress_input_invalid$"):
            golden_diff(*mappings)


def test_expectations_separate_keys(pair):
    b, _, specs = pair
    digests = {
        o.task_id: digest_for(specs[o.task_id], o)
        for o in b.observations
        if o.task_id in GOLDEN_TASKS
    }
    expectations = golden_expectations(specs.values())
    assert expectation_mismatches(expectations, digests) == {}
    expectations["T1-01"].update(
        refused=True, forbidden_tools=["lookup_id"], effect_count=9, status="failed"
    )
    assert expectation_mismatches(expectations, digests) == {
        "T1-01": ["effect_count", "status", "forbidden_tool_called"]
    }


def test_aggregate_equals_metrics_and_json_details(pair):
    b, c, specs = pair
    rows = details_for(pair)
    v = regression.reduce_details(rows, b, c, role="control")
    metrics = asdict(
        summarize(
            [
                evaluate_run(specs[o.task_id], o, result_dir="synthetic")
                for o in c.observations
            ]
        )
    )
    values = v["criteria"]["4"]["values"]
    assert values["candidate_required"] == {
        k: metrics["required_tool_call_rate"][k] for k in ("numerator", "denominator")
    }
    assert values["candidate_ok"] == metrics["call_ok_rate"]
    decoded = [json.loads(json.dumps(asdict(row))) for row in rows]
    assert v["criteria"]["2"]["values"]["candidate_passed"] == sum(
        r["candidate"]["passed"] for r in decoded
    )
    assert v["criteria"]["3"]["values"]["candidate_count"] == sum(
        len(r["candidate"]["forbidden_reasons"]) for r in decoded
    )
    assert values["candidate_required"]["numerator"] == sum(
        r["candidate"]["required_met"] is True for r in decoded
    )
    assert values["candidate_ok"]["total"]["denominator"] == sum(
        r["candidate"]["reads_total"] + r["candidate"]["writes_total"] for r in decoded
    )


def test_denominators(pair):
    b, c, _ = pair
    rows = details_for(pair)
    for update in (
        {"required_met": None},
        {"reads_total": 0, "reads_ok": 0, "writes_total": 0, "writes_ok": 0},
    ):
        changed = [replace(r, candidate=replace(r.candidate, **update)) for r in rows]
        with pytest.raises(ValueError, match="^m8_regress_denominator_zero$"):
            regression.reduce_details(changed, b, c, role="control")
    with pytest.raises(ValueError, match="^m8_regress_denominator_zero$"):
        regression.reduce_details(
            [replace(r, is_golden=False) for r in rows], b, c, role="control"
        )


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()


def registration_repo(root):
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.email", "synthetic@example.invalid")
    git(root, "config", "user.name", "Synthetic")
    for name in (REGISTRATION, "docs/results/hypotheses-m8-toolcall.md"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic registration\n")
    git(root, "add", "docs")
    git(root, "commit", "-m", "docs: register synthetic contract")
    return root


@pytest.fixture
def cli(tmp_path, monkeypatch):
    repo = registration_repo(tmp_path / "repo")
    baseline = build_synthetic_run(tmp_path, name=regression.BASELINE_RUN_ID)
    candidate = build_synthetic_run(tmp_path, name="candidate")
    monkeypatch.setattr(
        regression,
        "BASELINE_MANIFEST_SHA256",
        hashlib.sha256((baseline / "manifest.json").read_bytes()).hexdigest(),
    )
    specs, expectations = tmp_path / "specs.json", tmp_path / "expectations.json"
    write_json(specs, [s.model_dump() for s in synthetic_specs().values()])
    write_json(expectations, golden_expectations(synthetic_specs().values()))
    out = tmp_path / "output"
    args = [
        "compare",
        "--role",
        "control",
        "--baseline",
        str(baseline),
        "--candidate",
        str(candidate),
        "--out",
        str(out),
        "--specs",
        str(specs),
        "--expectations",
        str(expectations),
    ]
    return repo, baseline, candidate, out, args


def test_cli_roundtrip_render_and_private_fields(cli, capsys, monkeypatch):
    repo, baseline, candidate, out, args = cli
    monkeypatch.setattr(subprocess, "_USE_POSIX_SPAWN", False)
    # A genuinely empty process environment is also passed to git.
    monkeypatch.setattr("os.environ", {})
    assert regression.main(args, repo_root=repo) == 0
    stdout = capsys.readouterr().out
    v = json.loads((out / "verdict.json").read_text())
    assert (
        stdout.strip()
        == hashlib.sha256((out / "verdict.json").read_bytes()).hexdigest()
    )
    assert set(v["criteria"]) == set("123456")
    assert v["scope"] == "m8" and v["registration_commit"] == git(
        repo, "rev-parse", "HEAD"
    )
    text = (out / "verdict.md").read_text()
    rows = [line for line in text.splitlines() if line.startswith("|")]
    assert len(rows) == 8
    assert "13,832.05" in text
    for row in rows:
        assert len(row.split("|")) == len(rows[0].split("|"))
        assert all(cell.strip() for cell in row.split("|")[1:-1])
    for path in out.iterdir():
        assert all(marker not in path.read_text() for marker in SENTINELS)
    detail = [
        json.loads(line)
        for line in (out / "verdict-detail.jsonl").read_text().splitlines()
    ]
    assert v["criteria"]["2"]["values"]["candidate_passed"] == sum(
        r["candidate"]["passed"] for r in detail
    )
    verify = [
        "verify",
        "--verdict",
        str(out / "verdict.json"),
        "--baseline",
        str(baseline),
        "--candidate",
        str(candidate),
    ]
    assert regression.main(verify, repo_root=repo) == 0
    with (candidate / "observations.jsonl").open("a") as stream:
        stream.write("\n")
    assert regression.main(verify, repo_root=repo) == 1
    assert capsys.readouterr().out.strip() == "FAIL m8_regress_hash_mismatch"
    print(text)


@pytest.mark.parametrize(
    "failure",
    [
        "registration_not_committed",
        "baseline_mismatch",
        "input_missing",
        "input_invalid",
        "denominator_zero",
        "out_overlaps_input",
        "out_exists",
    ],
)
def test_cli_abort_codes(cli, capsys, failure):
    repo, baseline, candidate, out, args = cli
    if failure == "registration_not_committed":
        (repo / REGISTRATION).write_text("dirty")
    elif failure == "baseline_mismatch":
        (baseline / "manifest.json").write_text("{}")
    elif failure == "input_missing":
        (candidate / "grades.json").unlink()
    elif failure == "input_invalid":
        (candidate / "grades.json").write_text("[]")
    elif failure == "denominator_zero":
        specs = Path(args[args.index("--specs") + 1])
        data = json.loads(specs.read_text())
        for row in data:
            row["required_tools_any"] = []
        write_json(specs, data)
    elif failure == "out_overlaps_input":
        args[args.index("--out") + 1] = str(candidate / "out")
    elif failure == "out_exists":
        out.mkdir()
    assert regression.main(args, repo_root=repo) == 1
    assert capsys.readouterr().out.strip() == "FAIL m8_regress_" + failure


@pytest.mark.parametrize(
    "case", ["uncommitted", "committed", "dirty", "modified_commit", "timeout"]
)
def test_registration_matches_toolcall(tmp_path, monkeypatch, case):
    repo = registration_repo(tmp_path / "repo")
    paths = [REGISTRATION, "docs/results/hypotheses-m8-toolcall.md"]
    if case == "uncommitted":
        git(repo, "update-ref", "-d", "HEAD")
    if case in {"dirty", "modified_commit"}:
        for path in paths:
            (repo / path).write_text("changed")
    if case == "modified_commit":
        git(repo, "add", "docs")
        git(repo, "commit", "-m", "docs: modify registration")
    if case == "timeout":

        def timeout(*args, **kwargs):
            assert kwargs["timeout"] == 10
            raise subprocess.TimeoutExpired("git", 10)

        monkeypatch.setattr(subprocess, "run", timeout)
    outcomes = []
    for module in (regression, metrics_cli):
        try:
            outcomes.append((True, module._registration_commit(repo)))
        except (ValueError, metrics_cli._Failure) as exc:
            assert str(exc).endswith("registration_not_committed")
            outcomes.append((False, None))
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0] is (case == "committed")


def test_codes_and_no_environment_ast():
    source = inspect.getsource(regression)
    tree = ast.parse(source)
    assert not any(
        isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "os"
        and n.attr in {"environ", "getenv"}
        for n in ast.walk(tree)
    )
    document = (Path(__file__).resolve().parents[1] / REGISTRATION).read_text()
    codes = set(
        re.findall(
            r'"((?:m8_regress_|invalid_)[a-z_]+|tcr_drop|forbidden_violation|toolcall_required_drop|toolcall_ok_drop|golden_structure|cost_over|latency_over)"',
            source,
        )
    )
    document = document.split("## 8.", 1)[1].split("## 11.", 1)[0]
    registered = set(
        re.findall(
            r"`((?:m8_regress_|invalid_)[a-z_]+|tcr_drop|forbidden_violation|toolcall_required_drop|toolcall_ok_drop|golden_structure|cost_over|latency_over)`",
            document,
        )
    )
    assert codes <= registered
    assert {c for c in registered if not c.startswith("m8_regress_")} <= codes


@pytest.mark.parametrize(
    "case",
    [
        "extra_field",
        "run_index",
        "duplicate",
        "task_set",
        "trace_missing",
        "trace_invalid",
        "specs_missing_golden",
        "expectations_invalid",
        "specs_invalid",
    ],
)
def test_input_validation_at_cli(cli, capsys, case):
    repo, _baseline, candidate, _out, args = cli
    path = candidate / "observations.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    expected = "m8_regress_input_invalid"
    if case == "extra_field":
        rows[0]["unknown"] = SENTINELS[0]
    elif case == "run_index":
        rows[0]["run_index"] = 1
    elif case == "duplicate":
        rows.append(rows[0])
    elif case == "task_set":
        rows.pop()
    elif case == "trace_missing":
        next((candidate / "traces").iterdir()).unlink()
        expected = "m8_regress_input_missing"
    elif case == "trace_invalid":
        next((candidate / "traces").iterdir()).write_text("[]\n")
    elif case == "specs_missing_golden":
        path_spec = Path(args[args.index("--specs") + 1])
        write_json(
            path_spec,
            [s.model_dump() for key, s in synthetic_specs().items() if key != "T1-01"],
        )
    elif case == "specs_invalid":
        Path(args[args.index("--specs") + 1]).write_text('[{"task_id": "private"}]')
    elif case == "expectations_invalid":
        Path(args[args.index("--expectations") + 1]).write_text('{"T1-01": []}')
    write_jsonl(path, rows)
    assert regression.main(args, repo_root=repo) == 1
    assert capsys.readouterr().out.strip() == "FAIL " + expected


def test_baseline_directory_identity(cli, capsys):
    repo, _baseline, candidate, _out, args = cli
    args[args.index("--baseline") + 1] = str(candidate)
    assert regression.main(args, repo_root=repo) == 1
    assert capsys.readouterr().out.strip() == "FAIL m8_regress_baseline_mismatch"


def test_forbidden_record_and_golden_status_flow(pair):
    b, c, specs = pair
    # These reasons come exclusively from saved TCR reasons, never from rules.
    grade = c.grades["T2-09"]
    for reason in regression.FORBIDDEN_REASONS:
        grades = c.grades | {
            "T2-09": grade
            | {"tcr": grade["tcr"] | {"reasons": [reason], "passed": False}}
        }
        assert verdict((b, replace(c, grades=grades), specs))["reasons"] == [
            "forbidden_violation"
        ]
    obs = [
        o.model_copy(update={"status": "failed"}) if o.task_id == "T1-01" else o
        for o in c.observations
    ]
    assert verdict((b, replace(c, observations=obs), specs))["reasons"] == [
        "golden_structure"
    ]


def test_all_model_call_evidence_and_zero_split_denominators(pair):
    b, c, _specs = pair
    rows = details_for(pair)
    for update in (
        {"reads_total": 0, "reads_ok": 0},
        {"writes_total": 0, "writes_ok": 0},
    ):
        with pytest.raises(ValueError, match="^m8_regress_denominator_zero$"):
            regression.reduce_details(
                [replace(r, candidate=replace(r.candidate, **update)) for r in rows],
                b,
                c,
                role="control",
            )


def test_missing_deployment_and_warning_only(pair):
    b, c, specs = pair
    assert verdict((b, replace(c, model_calls=[{}]), specs))["reasons"] == [
        "invalid_run",
        "invalid_deployment_evidence",
    ]
    warning = verdict(
        (b, replace(c, manifest=c.manifest | {"a_git_head": "0" * 40}), specs)
    )
    assert warning["verdict"] == "PASS" and warning["warnings"] == [
        "a_git_head_changed"
    ]


def test_unexpected_exception_is_private(cli, monkeypatch, capsys):
    repo, *_paths, args = cli

    def unexpected(*args, **kwargs):
        raise RuntimeError(SENTINELS[0])

    monkeypatch.setattr(regression, "load_run", unexpected)
    assert regression.main(args, repo_root=repo) == 1
    assert capsys.readouterr().out.strip() == "FAIL unexpected RuntimeError"


def test_output_aggregates_rebuild_from_detail_json(cli, capsys):
    repo, _b, _c, out, args = cli
    assert regression.main(args, repo_root=repo) == 0
    capsys.readouterr()
    rows = [
        json.loads(line)
        for line in (out / "verdict-detail.jsonl").read_text().splitlines()
    ]
    criteria = json.loads((out / "verdict.json").read_text())["criteria"]
    for side in ("baseline", "candidate"):
        values = [row[side] for row in rows]
        assert criteria["2"]["values"][side + "_passed"] == sum(
            v["passed"] for v in values
        )
        assert criteria["3"]["values"][side + "_count"] == sum(
            len(v["forbidden_reasons"]) for v in values
        )
        rates = criteria["4"]["values"]
        assert rates[side + "_required"] == {
            "numerator": sum(v["required_met"] is True for v in values),
            "denominator": sum(v["required_met"] is not None for v in values),
        }
        for split in ("reads", "writes"):
            assert rates[side + "_ok"][split] == {
                "numerator": sum(v[split + "_ok"] for v in values),
                "denominator": sum(v[split + "_total"] for v in values),
            }
        assert (
            criteria["6"]["values"][side + "_p95_ms"]
            == sorted(v["latency_ms"] for v in values)[18]
        )
    hard = {
        r["task_id"]: r["golden_hard"]
        for r in rows
        if r["is_golden"] and r["baseline"]["passed"] and r["golden_hard"]
    }
    assert criteria["5"]["values"]["hard_mismatches"] == hard


def test_expectation_report_does_not_judge(cli, capsys):
    repo, _b, _c, out, args = cli
    path = Path(args[args.index("--expectations") + 1])
    expectations = json.loads(path.read_text())
    expectations["T1-01"]["effect_count"] = 99
    write_json(path, expectations)
    assert regression.main(args, repo_root=repo) == 0
    capsys.readouterr()
    v = json.loads((out / "verdict.json").read_text())
    assert v["verdict"] == "PASS"
    for side in ("baseline", "candidate"):
        assert v["criteria"]["5"]["values"][side + "_expectation_mismatches"][
            "T1-01"
        ] == ["effect_count"]


def test_empty_golden_denominator_and_unexpected_valueerror(
    pair, cli, monkeypatch, capsys
):
    b, c, _ = pair
    with pytest.raises(ValueError, match="^m8_regress_denominator_zero$"):
        regression.reduce_details([], b, c, role="control")
    repo, *_paths, args = cli

    def unexpected(*args, **kwargs):
        raise ValueError(SENTINELS[0])

    monkeypatch.setattr(regression, "load_run", unexpected)
    assert regression.main(args, repo_root=repo) == 1
    assert capsys.readouterr().out.strip() == "FAIL unexpected ValueError"


def test_all_trace_model_calls_are_checked(tmp_path):
    path = build_synthetic_run(tmp_path, name="run")
    manifest = json.loads((path / "manifest.json").read_text())
    trace = path / "traces" / (manifest["trace_ids"]["T1-01"] + ".jsonl")
    with trace.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "kind": "model_call",
                    "name": "agent-fallback",
                    "parent_id": "root",
                    "attributes": {"deployment_id": "other", "attempted_fallbacks": 1},
                }
            )
            + "\n"
        )
    candidate = regression.load_run(path)
    baseline = regression.load_run(build_synthetic_run(tmp_path, name="baseline"))
    v = verdict((baseline, candidate, synthetic_specs()))
    assert v["reasons"] == ["invalid_run", "invalid_deployment_evidence"]
    assert v["criteria"]["1"]["values"]["attempted_fallbacks"] == 1


@pytest.mark.parametrize("bad", [[], {}, {"inputs": []}, {"inputs": {"baseline": {}}}])
def test_verify_invalid_schema_is_input_invalid(cli, bad, capsys):
    repo, baseline, candidate, out, _args = cli
    out.mkdir()
    write_json(out / "verdict.json", bad)
    assert (
        regression.main(
            [
                "verify",
                "--verdict",
                str(out / "verdict.json"),
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
            ],
            repo_root=repo,
        )
        == 1
    )
    assert capsys.readouterr().out.strip() == "FAIL m8_regress_input_invalid"


def test_golden_expectations_detect_digest_forbidden_flag():
    expected = {
        "T1-01": {"effect_count": 0, "status": "completed", "forbidden_tools": []}
    }
    digests = {
        "T1-01": {
            "effect_count": 0,
            "status": "completed",
            "tool_order": [],
            "forbidden_tool_called": True,
            "refused": True,
        }
    }
    assert expectation_mismatches(expected, digests) == {
        "T1-01": ["forbidden_tool_called"]
    }


def test_verify_only_compares_input_hashes_after_relocation(cli, capsys):
    repo, baseline, candidate, out, args = cli
    assert regression.main(args, repo_root=repo) == 0
    capsys.readouterr()
    moved = candidate.rename(candidate.with_name("relocated-candidate"))
    assert (
        regression.main(
            [
                "verify",
                "--verdict",
                str(out / "verdict.json"),
                "--baseline",
                str(baseline),
                "--candidate",
                str(moved),
            ],
            repo_root=repo,
        )
        == 0
    )
    assert capsys.readouterr().out == ""
