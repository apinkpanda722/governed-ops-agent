"""Offline CLI contracts, using temporary git repositories and synthetic runs only."""

import ast
import hashlib
import json
import re
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest
from test_metrics_toolcall import spec

from govops.eval.observation import load_observations
from govops.metrics import cli
from govops.metrics.toolcall import RunKey, RunToolcall, summarize
from govops.metrics.traces import ReadSpan, link_reads, read_tool_spans

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = "docs/results/hypotheses-m8-toolcall.md"
MARKERS = ("SYN_ANSWER_PRIVATE", "SYN_EFFECT_PRIVATE", "SYN_RESULT_PRIVATE")


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    path = root / REGISTRATION
    path.parent.mkdir(parents=True)
    path.write_text("synthetic preregistration\n")
    git(root, "add", REGISTRATION)
    git(
        root,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=syn@example.invalid",
        "commit",
        "-qm",
        "docs: register synthetic metrics",
    )
    return root


def fixture_run(tmp_path, name, *, count=1, repeat=None, run_index=0, calls=None):
    directory = tmp_path / name
    directory.mkdir()
    (directory / "attempts").mkdir()
    (directory / "traces").mkdir()
    rows, grades, trace_ids = [], {}, {}
    specs = []
    calls = (
        [
            ("search_docs", True),
            ("jira_get_ticket", False),
            ("slack_send_message", True),
        ]
        if calls is None
        else calls
    )
    for index in range(count):
        task = f"T1-{index + 1:02}"
        trace = f"{index + 1:032x}"
        trace_ids[task] = trace
        specs.append(spec(task_id=task).model_dump())
        rows.append(
            {
                "answer": MARKERS[0],
                "approvals": [],
                "cited_doc_ids": [],
                "effect_bodies": [MARKERS[1]],
                "effects": [],
                "fault_injected": False,
                "observed_ids": [],
                "policy_violations": [],
                "run_index": run_index,
                "status": "completed",
                "stop_reason": None,
                "task_id": task,
                "tool_calls": [{"name": n, "ok": ok} for n, ok in calls],
                "tool_results": [],
                "trace_id": trace,
            }
        )
        grades[task] = {
            "tcr": {
                "task_id": task,
                "run_index": run_index,
                "passed": True,
                "reasons": [],
                "tool_coverage": ("search_docs", True) in calls,
            },
            "rules": {
                "duplicate_count": 0,
                "fault_injected": False,
                "leak_candidates": [],
                "leak_confirmed": [],
                "leak_pending": [],
                "run_index": run_index,
                "task_id": task,
                "unapproved_effect_count": 0,
            },
            "used_auto_forbidden": False,
        }
        spans = [
            span(trace, "task", "task", {"output.answer": MARKERS[0]}),
            span(trace, "tool_call", "tool_attempt", {}),
        ]
        if repeat is not None:
            spans.append(span(trace, "task", "task", {}))
        spans += [
            span(
                trace,
                "tool_call",
                n,
                {
                    "success": ok,
                    "error_code": None if ok else "new_error_code",
                    "tool.result": MARKERS[2],
                },
            )
            for n, ok in calls
            if n != "slack_send_message"
        ]
        (directory / "traces" / f"{trace}.jsonl").write_text(
            "".join(json.dumps(s) + "\n" for s in reversed(spans))
        )
    (directory / "observations.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )
    write_json(directory / "grades.json", grades)
    manifest = {
        "trace_ids": trace_ids,
        "run_label": "N2" if repeat else name,
        "config": "v1",
        "run_role": "official" if repeat else "reference",
        "validity": "valid",
    }
    if repeat is not None:
        manifest["repeat_index"] = repeat
    write_json(directory / "manifest.json", manifest)
    specs_path = tmp_path / "specs.json"
    write_json(specs_path, specs)
    assert all(len(row) == 15 for row in rows)
    assert len(load_observations(directory / "observations.jsonl")) == count
    return directory, specs_path


def span(trace, kind, name, attributes):
    return {
        "attributes": attributes,
        "duration_ms": 1,
        "end_ts": 2,
        "kind": kind,
        "name": name,
        "parent_id": None,
        "span_id": "a" * 16,
        "start_ts": 1,
        "status": "ok",
        "trace_id": trace,
    }


def invoke(repo, directory, specs_path, out, *extra):
    return cli.main(
        [
            "toolcall",
            "--official",
            str(directory),
            "--specs",
            str(specs_path),
            "--out",
            str(out),
            *extra,
        ],
        repo_root=repo,
    )


def test_three_official_directories_and_separate_references(repo, tmp_path, capsys):
    official = [
        fixture_run(tmp_path, f"official-{i}", count=40, repeat=i)[0] for i in (1, 2, 3)
    ]
    refs = [fixture_run(tmp_path, n, count=40, run_index=2)[0] for n in ("N1a", "N1b")]
    out = tmp_path / "report"
    assert (
        cli.main(
            [
                "toolcall",
                "--official",
                *map(str, official),
                "--reference",
                *map(str, refs),
                "--specs",
                str(tmp_path / "specs.json"),
                "--out",
                str(out),
            ],
            repo_root=repo,
        )
        == 0
    )
    report = json.loads((out / "report.json").read_text())
    raw_rows = [
        json.loads(line) for line in (out / "rows.jsonl").read_text().splitlines()
    ]
    runs = [
        RunToolcall(
            RunKey(r["result_dir"], r["task_id"], r["run_index"]),
            **{
                k: r[k]
                for k in (
                    "required_groups",
                    "required_met",
                    "forbidden_calls",
                    "reads_total",
                    "reads_ok",
                    "writes_total",
                    "writes_ok",
                )
            },
        )
        for r in raw_rows
    ]
    assert report["scope"] == "m8"
    assert report["label"] == "M8 추가 측정 — 공식 M6 수치 아님"
    assert report["registration_commit"] == git(repo, "rev-parse", "HEAD")
    official_runs = [r for r in runs if r.key.result_dir.startswith("official-")]
    assert len(official_runs) == 120 and {r.key.run_index for r in official_runs} == {0}
    assert report["groups"]["official"] == asdict(summarize(official_runs))
    assert set(report["groups"]["reference"]) == {"N1a", "N1b"}
    for directory in (*official, *refs):
        selected = [r for r in runs if r.key.result_dir == directory.name]
        assert report["directories"][directory.name]["summary"] == asdict(
            summarize(selected)
        )
        hashes = report["inputs"][str(directory.resolve())]
        for filename in ("observations.jsonl", "grades.json", "manifest.json"):
            assert (
                hashes[filename]
                == hashlib.sha256((directory / filename).read_bytes()).hexdigest()
            )
        entries = "".join(
            f"{p.name}\0{hashlib.sha256(p.read_bytes()).hexdigest()}\n"
            for p in sorted((directory / "traces").iterdir())
        )
        assert hashes["traces"] == hashlib.sha256(entries.encode()).hexdigest()
    assert report["connections"]["error_codes"] == {"new_error_code": 200}
    md = (out / "report.md").read_text()
    tables = [line for line in md.splitlines() if line.startswith("|")]
    assert tables
    assert len({len(line.split("|")) for line in tables}) == 1
    assert all(cell.strip() for line in tables for cell in line.split("|")[1:-1])
    captured = capsys.readouterr()
    combined = (
        captured.out
        + captured.err
        + md
        + (out / "report.json").read_text()
        + (out / "rows.jsonl").read_text()
    )
    assert not any(marker in combined for marker in MARKERS)
    print(md)  # Human preview with pytest -s; no effect on verdict.
    assert cli.main(["verify", "--report", str(out / "report.json")]) == 0


@pytest.mark.parametrize(
    "mode", ["match", "mismatch", "missing", "bad_json", "unreadable", "write_only"]
)
def test_trace_connection_modes(repo, tmp_path, monkeypatch, mode):
    directory, specs_path = fixture_run(
        tmp_path, "run", count=2 if mode == "write_only" else 1
    )
    trace = directory / "traces" / f"{1:032x}.jsonl"
    if mode == "mismatch":
        trace.write_text(
            json.dumps(span("a" * 32, "tool_call", "lookup_id", {"success": True}))
        )
    elif mode == "missing":
        trace.unlink()
    elif mode == "bad_json":
        with trace.open("a") as handle:
            handle.write("{bad\n")
    elif mode == "unreadable":

        def unreadable(path):
            raise PermissionError("synthetic")

        monkeypatch.setattr(cli, "read_tool_spans", unreadable)
    elif mode == "write_only":
        obs_path = directory / "observations.jsonl"
        observations = [json.loads(line) for line in obs_path.read_text().splitlines()]
        observations[0]["tool_calls"] = [{"name": "slack_send_message", "ok": True}]
        obs_path.write_text("".join(json.dumps(row) + "\n" for row in observations))
        grades = json.loads((directory / "grades.json").read_text())
        grades["T1-01"]["tcr"]["tool_coverage"] = False
        write_json(directory / "grades.json", grades)
        trace.write_text(json.dumps(span("a" * 32, "tool_call", "tool_attempt", {})))
        assert read_tool_spans(trace) == []
    assert invoke(repo, directory, specs_path, tmp_path / "out") == 0
    report = json.loads((tmp_path / "out/report.json").read_text())
    connection = report["connections"]
    assert connection["unlinked_write"] == (2 if mode == "write_only" else 1)
    assert connection["unlinked_read"] == (0 if mode in {"match", "write_only"} else 2)
    assert connection["unlinked_trace_missing"] == int(
        mode in {"missing", "bad_json", "unreadable"}
    )
    assert connection["error_codes"] == (
        {"new_error_code": 1} if mode in {"match", "write_only"} else {}
    )
    if mode == "write_only":
        rows = [
            json.loads(line)
            for line in (tmp_path / "out/rows.jsonl").read_text().splitlines()
        ]
        assert rows[0]["connection"] == {
            "linked_reads": 0,
            "unlinked_read": 0,
            "unlinked_trace_missing": 0,
            "unlinked_write": 1,
            "error_codes": {},
        }


def test_multisets_require_counts_and_success_and_ignore_order():
    spans = [ReadSpan("read", True, None), ReadSpan("read", False, "arbitrary")]
    assert link_reads([("read", False), ("read", True)], spans).error_codes == {
        "arbitrary": 1
    }
    for calls in [[("read", True)], [("read", True)] * 2]:
        result = link_reads(calls, spans)
        assert result.unlinked_read == len(calls) and result.error_codes == {}


@pytest.mark.parametrize(
    "change,code",
    [
        ("missing", "input_missing"),
        ("invalid", "input_invalid"),
        ("unknown", "unknown_task"),
        ("duplicate", "duplicate_run"),
        ("grade", "grade_mismatch"),
        ("zero", "denominator_zero"),
        ("overlap", "out_overlaps_input"),
        ("exists", "out_exists"),
    ],
)
def test_fixed_failures(repo, tmp_path, capsys, change, code):
    directory, specs_path = fixture_run(tmp_path, "run")
    obs = directory / "observations.jsonl"
    out = tmp_path / "out"
    if change == "missing":
        obs.unlink()
    elif change == "invalid":
        write_json(directory / "manifest.json", [])
    elif change == "unknown":
        write_json(specs_path, [])
    elif change == "duplicate":
        obs.write_text(obs.read_text() * 2)
    elif change == "grade":
        grades = json.loads((directory / "grades.json").read_text())
        grades["T1-01"]["tcr"]["tool_coverage"] = False
        write_json(directory / "grades.json", grades)
    elif change == "zero":
        specs = json.loads(specs_path.read_text())
        specs[0]["required_tools_any"] = []
        write_json(specs_path, specs)
    elif change == "overlap":
        out = directory / "nested"
    else:
        out.mkdir()
    assert invoke(repo, directory, specs_path, out) == 1
    captured = capsys.readouterr()
    assert captured.out + captured.err == f"FAIL m8_toolcall_{code}\n"


@pytest.mark.parametrize(
    "change", ["uncommitted", "edited", "staged", "later_commit", "timeout"]
)
def test_registration_gate(repo, tmp_path, capsys, monkeypatch, change):
    directory, specs_path = fixture_run(tmp_path, "run")
    if change == "uncommitted":
        repo = tmp_path / "empty"
        repo.mkdir()
        git(repo, "init", "-q")
    elif change == "timeout":

        def timeout(*args, **kwargs):
            assert kwargs["timeout"] == 10
            raise subprocess.TimeoutExpired("git", 10)

        monkeypatch.setattr(cli.subprocess, "run", timeout)
    else:
        (repo / REGISTRATION).write_text("edited\n")
        if change in {"staged", "later_commit"}:
            git(repo, "add", REGISTRATION)
        if change == "later_commit":
            git(
                repo,
                "-c",
                "user.name=Synthetic",
                "-c",
                "user.email=syn@example.invalid",
                "commit",
                "-qm",
                "docs: change registration",
            )
    assert invoke(repo, directory, specs_path, tmp_path / "out") == 1
    captured = capsys.readouterr()
    assert (
        captured.out + captured.err == "FAIL m8_toolcall_registration_not_committed\n"
    )


@pytest.mark.parametrize(
    "filename", ["observations.jsonl", "grades.json", "manifest.json", "trace", "specs"]
)
def test_verify_detects_input_tampering(repo, tmp_path, capsys, filename):
    directory, specs_path = fixture_run(tmp_path, "run")
    out = tmp_path / "out"
    assert invoke(repo, directory, specs_path, out) == 0
    capsys.readouterr()
    path = (
        next((directory / "traces").iterdir())
        if filename == "trace"
        else specs_path
        if filename == "specs"
        else directory / filename
    )
    with path.open("a") as handle:
        handle.write(" ")
    assert cli.main(["verify", "--report", str(out / "report.json")]) == 1
    captured = capsys.readouterr()
    assert captured.out + captured.err == "FAIL m8_toolcall_hash_mismatch\n"


def test_empty_environment_and_source_contract(repo, tmp_path, monkeypatch):
    import os

    for key in list(os.environ):
        monkeypatch.delenv(key)
    directory, specs_path = fixture_run(tmp_path, "run")
    assert invoke(repo, directory, specs_path, tmp_path / "out") == 0
    sources = [
        ROOT / f"src/govops/metrics/{name}.py" for name in ("cli", "traces", "toolcall")
    ]
    codes = set()
    for path in sources:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            assert not (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "os"
                and node.attr in {"environ", "getenv"}
            )
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith("m8_toolcall_")
            ):
                codes.add(node.value)
    fixed = set(
        re.findall(
            r"^\| `(m8_toolcall_\w+)` \|",
            (ROOT / REGISTRATION).read_text(),
            re.MULTILINE,
        )
    )
    assert codes == fixed


def test_unexpected_exception_only_names_class(repo, tmp_path, capsys, monkeypatch):
    directory, specs_path = fixture_run(tmp_path, "run")

    def broken(*args, **kwargs):
        raise RuntimeError(MARKERS[0])

    monkeypatch.setattr(cli, "evaluate_run", broken)
    assert invoke(repo, directory, specs_path, tmp_path / "out") == 1
    captured = capsys.readouterr()
    assert captured.out + captured.err == "FAIL unexpected RuntimeError\n"


@pytest.mark.parametrize(
    "filename,content",
    [
        ("observations.jsonl", "{broken"),
        ("observations.jsonl", '{"task_id":"T1-01","extra":"forbidden"}'),
        ("grades.json", "[]"),
        ("grades.json", '{"T1-01":{"tcr":null}}'),
        ("manifest.json", '{"trace_ids":[]}'),
        ("specs.json", "{}"),
        ("specs.json", "[{}]"),
    ],
)
def test_invalid_inputs_have_safe_fixed_code(repo, tmp_path, capsys, filename, content):
    directory, specs_path = fixture_run(tmp_path, "run")
    path = specs_path if filename == "specs.json" else directory / filename
    path.write_text(content)
    out = tmp_path / "out"
    assert invoke(repo, directory, specs_path, out) == 1
    captured = capsys.readouterr()
    assert captured.out + captured.err == "FAIL m8_toolcall_input_invalid\n"
    assert not out.exists()


@pytest.mark.parametrize("target", ["specs", "ancestor", "symlink"])
def test_overlaps_are_detected_before_reading_inputs(repo, tmp_path, capsys, target):
    directory, specs_path = fixture_run(tmp_path, "run")
    out = specs_path if target == "specs" else tmp_path
    if target == "symlink":
        out = tmp_path / "alias"
        out.symlink_to(directory, target_is_directory=True)
    assert invoke(repo, directory, specs_path, out) == 1
    captured = capsys.readouterr()
    assert captured.out + captured.err == "FAIL m8_toolcall_out_overlaps_input\n"


def test_existing_report_is_never_overwritten(repo, tmp_path, capsys):
    directory, specs_path = fixture_run(tmp_path, "run")
    out = tmp_path / "out"
    assert invoke(repo, directory, specs_path, out) == 0
    before = {path.name: path.read_bytes() for path in out.iterdir()}
    assert invoke(repo, directory, specs_path, out) == 1
    assert {path.name: path.read_bytes() for path in out.iterdir()} == before
    captured = capsys.readouterr()
    assert captured.out + captured.err == "FAIL m8_toolcall_out_exists\n"


def test_reference_only_and_unrelated_commit_preserve_registration(repo, tmp_path):
    directory, specs_path = fixture_run(tmp_path, "N1a")
    commit = git(repo, "rev-parse", "HEAD")
    (repo / "unrelated").write_text("synthetic")
    git(repo, "add", "unrelated")
    git(
        repo,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=syn@example.invalid",
        "commit",
        "-qm",
        "docs: unrelated synthetic change",
    )
    out = tmp_path / "out"
    assert (
        cli.main(
            [
                "toolcall",
                "--reference",
                str(directory),
                "--specs",
                str(specs_path),
                "--out",
                str(out),
            ],
            repo_root=repo,
        )
        == 0
    )
    report = json.loads((out / "report.json").read_text())
    assert report["registration_commit"] == commit
    assert report["groups"]["official"] is None
    assert set(report["groups"]["reference"]) == {"N1a"}


def test_changes_during_evaluation_do_not_publish_report(
    repo, tmp_path, capsys, monkeypatch
):
    directory, specs_path = fixture_run(tmp_path, "run")
    real_evaluate = cli.evaluate_run

    def mutate(*args, **kwargs):
        with (directory / "observations.jsonl").open("a") as handle:
            handle.write(" ")
        return real_evaluate(*args, **kwargs)

    monkeypatch.setattr(cli, "evaluate_run", mutate)
    out = tmp_path / "out"
    assert invoke(repo, directory, specs_path, out) == 1
    captured = capsys.readouterr()
    assert captured.out + captured.err == "FAIL m8_toolcall_hash_mismatch\n"
    assert not out.exists()


def test_existing_dangling_output_symlink_is_preserved(repo, tmp_path, capsys):
    directory, specs_path = fixture_run(tmp_path, "run")
    out = tmp_path / "out"
    missing_target = tmp_path / "absent"
    out.symlink_to(missing_target, target_is_directory=True)
    assert invoke(repo, directory, specs_path, out) == 1
    captured = capsys.readouterr()
    assert captured.out + captured.err == "FAIL m8_toolcall_out_exists\n"
    assert out.is_symlink() and not missing_target.exists()
