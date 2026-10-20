"""M8 tool-call metrics use synthetic specs and observations only."""

import ast
import random
import re
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path

import pytest

from govops.eval.observation import Observation, ToolCallRecord
from govops.eval.spec import TaskSpec
from govops.eval.tcr import grade_tcr
from govops.golden import _WRITES
from govops.metrics.toolcall import (
    WRITE_TOOLS,
    RunKey,
    cross_check_tool_coverage,
    evaluate_run,
    summarize,
)

ROOT = Path(__file__).resolve().parents[1]
READS = ("search_docs", "lookup_id", "get_document", "jira_get_ticket", "run_sql")
WRITES = ("slack_send_message", "jira_add_comment", "jira_create_followup_ticket")


def spec(*, task_id="T1-01", groups=None, forbidden=()):
    return TaskSpec.model_validate(
        {
            "task_id": task_id,
            "type": "T1",
            "subtype": "incident_summary",
            "split": "test",
            "executable_in": "m2",
            "user": "demo-platform-g3",
            "request": "합성 장애 요약",
            "expected": {"outcome": "answer", "effects": []},
            "required_tools_any": [["search_docs"]] if groups is None else groups,
            "forbidden": {
                "leak_doc_ids": [],
                "leak_ticket_ids": [],
                "leak_values": [],
                "forbidden_tools": list(forbidden),
            },
            "required_facts": [],
            "derived_values": [],
            "approval_script": [],
            "provenance": {
                "meta_sha256": "a" * 64,
                "source_doc_ids": [],
                "source_ticket_ids": [],
            },
            "spec_sha256": "b" * 64,
        }
    )


def observation(calls=None, *, task_id="T1-01", run_index=0):
    return Observation(
        task_id=task_id,
        run_index=run_index,
        status="completed",
        answer="합성 요약",
        observed_ids=[],
        cited_doc_ids=[],
        tool_calls=[
            ToolCallRecord(name=name, ok=ok)
            for name, ok in (
                [("search_docs", True), ("slack_send_message", True)]
                if calls is None
                else calls
            )
        ],
        effects=[],
        effect_bodies=[],
        approvals=[],
        policy_violations=[],
        fault_injected=False,
        trace_id=None,
    )


def run(*, result_dir="synthetic-1", task_id="T1-01", run_index=0):
    return evaluate_run(
        spec(task_id=task_id),
        observation(task_id=task_id, run_index=run_index),
        result_dir=result_dir,
    )


def grades_for(runs):
    grades = {}
    for row in runs:
        grades.setdefault(row.key.result_dir, {})[row.key.task_id] = {
            "tcr": {
                "task_id": row.key.task_id,
                "run_index": row.key.run_index,
                "passed": True,
                "reasons": [],
                "tool_coverage": row.required_met is not False,
            },
            "rules": {},
            "used_auto_forbidden": False,
        }
    return grades


@pytest.mark.parametrize(
    "calls,expected",
    [
        ([("search_docs", True)], False),
        ([("search_docs", True), ("get_document", False)], False),
        ([("lookup_id", True), ("get_document", True)], True),
    ],
)
def test_required_groups_are_and_of_successful_alternatives(calls, expected):
    row = evaluate_run(
        spec(groups=[["search_docs", "lookup_id"], ["get_document"]]),
        observation(calls),
        result_dir="synthetic-1",
    )
    assert row.required_groups == 2
    assert row.required_met is expected


def test_empty_group_list_is_excluded_in_summary_but_cross_checked_as_true():
    empty = evaluate_run(spec(groups=[]), observation(), result_dir="synthetic-2")
    assert empty.required_groups == 0 and empty.required_met is None
    failed = replace(run(result_dir="synthetic-3"), required_met=False)
    summary = summarize([run(), empty, failed])
    assert asdict(summary.required_tool_call_rate) == {
        "numerator": 1,
        "denominator": 2,
        "excluded_empty_groups": 1,
    }
    assert cross_check_tool_coverage([empty], grades_for([empty])) == []
    grades = grades_for([empty])
    grades[empty.key.result_dir][empty.key.task_id]["tcr"]["tool_coverage"] = False
    assert [m.key for m in cross_check_tool_coverage([empty], grades)] == [empty.key]


def test_forbidden_calls_count_occurrences_including_failed_calls():
    forbidden = evaluate_run(
        spec(forbidden=["slack_send_message"]),
        observation(
            [
                ("search_docs", True),
                ("slack_send_message", True),
                ("slack_send_message", False),
            ]
        ),
        result_dir="synthetic-1",
    )
    assert forbidden.forbidden_calls == 2
    summary = summarize([forbidden, run(result_dir="synthetic-2")])
    assert asdict(summary.forbidden_call_count) == {
        "count": 2,
        "forbidden_call_runs": 1,
        "total_runs": 2,
    }


def test_reads_and_submitted_write_drafts_have_separate_denominators():
    calls = [
        ("search_docs", True),
        ("slack_send_message", False),  # Graph-rejected draft, no effect executed.
        ("jira_add_comment", True),
        ("jira_create_followup_ticket", True),
        ("lookup_id", True),
        ("jira_get_ticket", False),
        ("run_sql", True),
        ("get_document", False),  # Unmatched captured read appended at the end.
    ]
    obs = observation(calls)
    row = evaluate_run(spec(), obs, result_dir="synthetic-1")
    assert obs.effects == []
    assert (row.reads_ok, row.reads_total) == (3, 5)
    assert (row.writes_ok, row.writes_total) == (2, 3)
    rates = summarize([row]).call_ok_rate
    assert asdict(rates) == {
        "total": {"numerator": 5, "denominator": 8},
        "reads": {"numerator": 3, "denominator": 5},
        "writes": {"numerator": 2, "denominator": 3},
    }
    assert rates.total.numerator == rates.reads.numerator + rates.writes.numerator
    assert rates.total.denominator == rates.reads.denominator + rates.writes.denominator


def test_three_directories_share_40_task_keys_without_duplicates():
    rows = [
        run(result_dir=directory, task_id=f"T1-{task:02}")
        for directory in ("synthetic-1", "synthetic-2", "synthetic-3")
        for task in range(1, 41)
    ]
    assert len(rows) == 120
    assert {row.key.run_index for row in rows} == {0}
    summary = summarize(rows)
    assert summary.required_tool_call_rate.numerator == 120
    assert summary.required_tool_call_rate.denominator == 120
    assert summary.forbidden_call_count.total_runs == 120
    assert cross_check_tool_coverage(rows, grades_for(rows)) == []


def test_duplicate_full_run_key_is_rejected():
    row = run(run_index=2)
    with pytest.raises(ValueError, match="^m8_toolcall_duplicate_run$"):
        summarize([row, row])


@pytest.mark.parametrize("indices", [(2,), (0, 2)])
def test_run_indices_need_not_start_at_zero_or_be_contiguous(indices):
    rows = [run(run_index=index) for index in indices]
    assert summarize(rows).forbidden_call_count.total_runs == len(indices)
    # grades have one task row per directory; check each nonconsecutive run alone.
    for row in rows:
        assert cross_check_tool_coverage([row], grades_for([row])) == []


def test_100_seeded_synthetic_pairs_match_grade_tcr():
    rng = random.Random(20261020)
    names = (*READS, *WRITES)
    pairs = []
    for index in range(100):
        task = f"T1-{index + 1:03}"
        groups = [rng.sample(names, rng.randint(1, 3)) for _ in range(index % 4)]
        current_spec = spec(task_id=task, groups=groups, forbidden=rng.sample(names, 2))
        obs = observation(
            [(rng.choice(names), rng.choice((True, False))) for _ in range(index % 11)],
            task_id=task,
            run_index=rng.choice((0, 2, 5)),
        )
        pairs.append((current_spec, obs))
    assert len(pairs) == 100
    for current_spec, obs in pairs:
        row = evaluate_run(current_spec, obs, result_dir="synthetic-1")
        verdict = grade_tcr(current_spec, obs)
        assert (row.required_met is not False) is verdict.tool_coverage
        assert (row.forbidden_calls > 0) == ("forbidden_tool_called" in verdict.reasons)


@pytest.mark.parametrize(
    "change", ["coverage", "run_index", "missing_task", "missing_dir"]
)
def test_cross_check_reports_mismatch_with_full_run_key(change):
    rows = [run(result_dir=f"synthetic-{i}", run_index=2) for i in (1, 2, 3)]
    grades = grades_for(rows)
    key = rows[1].key
    if change == "coverage":
        grades[key.result_dir][key.task_id]["tcr"]["tool_coverage"] = False
    elif change == "run_index":
        grades[key.result_dir][key.task_id]["tcr"]["run_index"] = 0
    elif change == "missing_task":
        del grades[key.result_dir][key.task_id]
    else:
        del grades[key.result_dir]
    mismatches = cross_check_tool_coverage(rows, grades)
    assert [item.key for item in mismatches] == [key]


@pytest.mark.parametrize("case", ["empty", "required", "total", "reads", "writes"])
def test_all_summary_zero_denominators_fail(case):
    rows = [run()]
    if case == "empty":
        rows = []
    elif case == "required":
        rows = [evaluate_run(spec(groups=[]), observation(), result_dir="synthetic-1")]
    else:
        calls = {
            "total": [],
            "reads": [("slack_send_message", False)],
            "writes": [("search_docs", False)],
        }[case]
        rows = [evaluate_run(spec(), observation(calls), result_dir="synthetic-1")]
    with pytest.raises(ValueError, match="^m8_toolcall_denominator_zero$"):
        summarize(rows)


def test_cross_check_empty_input_fails():
    with pytest.raises(ValueError, match="^m8_toolcall_denominator_zero$"):
        cross_check_tool_coverage([], {})


def test_zero_numerators_with_nonzero_denominators_are_valid():
    row = evaluate_run(
        spec(),
        observation([("search_docs", False), ("slack_send_message", False)]),
        result_dir="synthetic-1",
    )
    summary = summarize([row])
    assert summary.required_tool_call_rate.numerator == 0
    assert summary.required_tool_call_rate.denominator == 1
    assert summary.forbidden_call_count.count == 0
    assert summary.forbidden_call_count.forbidden_call_runs == 0
    assert summary.forbidden_call_count.total_runs == 1
    assert asdict(summary.call_ok_rate) == {
        "total": {"numerator": 0, "denominator": 2},
        "reads": {"numerator": 0, "denominator": 1},
        "writes": {"numerator": 0, "denominator": 1},
    }


def test_write_tools_match_golden_and_values_remain_immutable_integers():
    assert WRITE_TOOLS == _WRITES == frozenset(WRITES)
    row = run()
    assert row.key == RunKey("synthetic-1", "T1-01", 0)
    with pytest.raises(FrozenInstanceError):
        row.required_met = False
    with pytest.raises(FrozenInstanceError):
        row.key.run_index = 2
    summary = asdict(summarize([row]))
    for metric in summary.values():
        for value in metric.values():
            if isinstance(value, dict):
                assert all(type(number) is int for number in value.values())
            else:
                assert type(value) is int


def test_module_is_pure_and_error_codes_are_preregistered():
    source = (ROOT / "src/govops/metrics/toolcall.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        assert not (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
            and node.attr == "environ"
        )
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else None
            attribute = node.func.attr if isinstance(node.func, ast.Attribute) else None
            assert name not in {"open", "Path"}
            assert attribute not in {"open", "Path"}
    codes = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("m8_toolcall_")
    }
    registration = (ROOT / "docs/results/hypotheses-m8-toolcall.md").read_text()
    fixed_codes = set(
        re.findall(r"^\| `(m8_toolcall_\w+)` \|", registration, re.MULTILINE)
    )
    assert codes and codes <= fixed_codes


def test_metrics_layer_allows_eval_and_blocks_producers_and_consumers(tmp_path):
    from test_architecture import (
        FORBIDDEN_IMPORTS,
        LAYERS,
        LLM_SDK,
        METRICS_CONSUMERS,
        find_violations,
    )

    assert METRICS_CONSUMERS <= LAYERS - {"metrics"}
    forbidden = {
        "govops.agent",
        "govops.api",
        "govops.broker",
        "govops.gateway",
        "govops.tools",
        "govops.mocks",
        "govops.evalrun",
        "govops.grounding",
        "langgraph",
        *LLM_SDK,
    }
    assert FORBIDDEN_IMPORTS["metrics"] == forbidden
    metrics = tmp_path / "metrics"
    metrics.mkdir()
    source = metrics / "toolcall.py"
    source.write_text("from govops.eval.spec import TaskSpec\n")
    assert find_violations(tmp_path) == []
    source.write_text("\n".join(f"import {name}" for name in sorted(forbidden)))
    assert {item.target for item in find_violations(tmp_path)} == forbidden
    source.write_text("")
    for layer in LAYERS - {"metrics"}:
        directory = tmp_path / layer
        directory.mkdir()
        (directory / "consumer.py").write_text("from govops.metrics import toolcall\n")
    violations = find_violations(tmp_path)
    assert {item.layer for item in violations} == LAYERS - {
        "metrics"
    } - METRICS_CONSUMERS
    assert {item.target for item in violations} == {"govops.metrics"}
