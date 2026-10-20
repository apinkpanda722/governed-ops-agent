"""Argument ID grounding with synthetic checkpoints only; argument text stays private."""

import json
import re
import shlex
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_metrics_toolcall import spec

from govops.eval.observation import Observation
from govops.grounding import argument

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = "docs/results/hypotheses-m8-argument.md"
PRIVATE = "SYN_PRIVATE_FREE_TEXT"
DOC = "syn-guide-001"
OTHER_DOC = "syn-guide-002"
INCIDENT = "INC-2026-001"
TICKET = "OPS-1234"


def call(call_id, name, arguments):
    text = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {"id": call_id, "function": {"name": name, "arguments": text}}


def assistant(*calls):
    return {"role": "assistant", "content": None, "tool_calls": list(calls)}


def tool(call_id, content):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def facts(messages, request="요청"):
    return argument.extract_call_facts(messages, request)


def test_ids_are_grounded_by_request_and_earlier_tool_messages_only():
    messages = [
        assistant(call("a", "search_docs", {"query": f"{PRIVATE} {DOC}"})),
        tool("a", f"결과 {OTHER_DOC} {TICKET}"),
        assistant(
            call("b", "get_document", {"doc_id": OTHER_DOC}),
            call("c", "jira_get_ticket", {"key": TICKET, "also": INCIDENT}),
        ),
        tool("b", f"{INCIDENT} 본문"),
        tool("c", "ok"),
        assistant(call("d", "get_document", {"doc_id": INCIDENT})),
    ]
    result = facts(messages, request=f"{DOC} 를 확인해줘")
    assert [(f["tool"], f["ids"], f["grounded"]) for f in result] == [
        ("search_docs", 1, 1),  # request grounds DOC
        ("get_document", 1, 1),  # earlier tool message grounds OTHER_DOC
        ("jira_get_ticket", 2, 1),  # INCIDENT appears only in a later result
        ("get_document", 1, 1),  # INCIDENT is now earlier evidence
    ]


def test_same_turn_results_do_not_ground_parallel_calls():
    messages = [
        assistant(
            call("a", "search_docs", {"query": "x"}),
            call("b", "get_document", {"doc_id": DOC}),
        ),
        tool("a", DOC),
        tool("b", DOC),
    ]
    assert [f["grounded"] for f in facts(messages)] == [0, 0]


def test_nested_values_dedupe_parse_failures_and_no_id_calls():
    messages = [
        assistant(
            call("a", "slack_send_message", {"text": f"{DOC} {DOC}", "x": [OTHER_DOC]}),
            call("b", "search_docs", "{not json"),
            call("c", "search_docs", {"query": "ID 없음", "n": 3}),
            call("d", "search_docs", "[1, 2]"),
        )
    ]
    result = facts(messages)
    assert [(f["ids"], f["parsed"]) for f in result] == [
        (2, True),
        (0, False),
        (0, True),
        (0, True),
    ]


def observation(task="T1-01", tool_calls=(), run_index=0):
    return Observation(
        task_id=task,
        run_index=run_index,
        status="completed",
        answer="a",
        observed_ids=[],
        cited_doc_ids=[],
        tool_calls=list(tool_calls),
        effects=[],
        effect_bodies=[],
        approvals=[],
        policy_violations=[],
        fault_injected=False,
        trace_id="1" * 32,
    )


def checkpoint(messages):
    return {"channel_values": {"messages": messages}}


def test_write_ok_is_paired_in_submission_order_and_unpaired_when_names_differ():
    calls = [
        {"name": "jira_add_comment", "ok": False},
        {"name": "search_docs", "ok": True},
        {"name": "slack_send_message", "ok": True},
    ]
    messages = [
        assistant(call("a", "jira_add_comment", {"k": DOC})),
        assistant(call("b", "search_docs", {"q": DOC})),
        assistant(call("c", "slack_send_message", {"t": DOC})),
        assistant(call("d", "jira_create_followup_ticket", {"t": DOC})),
    ]
    rows, run = argument.build_rows(
        group="official",
        result_dir="d",
        spec=spec(task_id="T1-01"),
        observation=observation(tool_calls=calls),
        checkpoints=[checkpoint(messages)],
    )
    assert [(r.tool, r.kind, r.ok) for r in rows] == [
        ("jira_add_comment", "write", False),
        ("search_docs", "read", None),
        ("slack_send_message", "write", True),
        ("jira_create_followup_ticket", "write", None),
    ]
    assert (run.observed_calls, run.assistant_calls) == (3, 4)


def test_first_thread_with_calls_is_used_and_multiple_are_counted():
    first = checkpoint([assistant(call("a", "search_docs", {"q": DOC}))])
    second = checkpoint([assistant(call("b", "search_docs", {"q": OTHER_DOC}))])
    empty = checkpoint([{"role": "user", "content": "x"}])
    rows, run = argument.build_rows(
        group="official",
        result_dir="d",
        spec=spec(task_id="T1-01"),
        observation=observation(),
        checkpoints=[None, empty, first, second],
    )
    assert [r.ids for r in rows] == [1] and run.multi_thread and run.checkpoint_missing
    rows, run = argument.build_rows(
        group="official",
        result_dir="d",
        spec=spec(task_id="T1-01"),
        observation=observation(),
        checkpoints=[],
    )
    assert rows == [] and run.unlinked and run.without_calls


def test_malformed_checkpoint_shape_has_a_fixed_code():
    for bad in ("text", {"channel_values": {"messages": "x"}}):
        with pytest.raises(argument._Failure, match="m8_args_checkpoint_shape"):
            argument.build_rows(
                group="official",
                result_dir="d",
                spec=spec(task_id="T1-01"),
                observation=observation(),
                checkpoints=[bad],
            )


def row(group, directory, kind, ids, grounded, *, ok=None, parsed=True):
    return {
        "group": group,
        "result_dir": directory,
        "task_id": "T1-01",
        "run_index": 0,
        "call_index": 0,
        "tool": "t",
        "kind": kind,
        "ok": ok,
        "arguments_ok": parsed,
        "ids": ids,
        "grounded": grounded,
    }


def run_record(group, directory, **overrides):
    value = {
        "group": group,
        "result_dir": directory,
        "task_id": "T1-01",
        "run_index": 0,
        "observed_calls": 1,
        "assistant_calls": 1,
        "unlinked": False,
        "checkpoint_missing": False,
        "without_calls": False,
        "multi_thread": False,
    }
    return value | overrides


def test_summary_splits_read_write_ok_and_never_merges_groups():
    rows = [
        row("official", "o1", "read", 2, 2),
        row("official", "o2", "read", 1, 0),
        row("official", "o2", "write", 1, 1, ok=True),
        row("official", "o1", "write", 0, 0, ok=True),
        row("reference", "r1", "read", 1, 1),
        row("reference", "r2", "write", 2, 1, ok=False),
        row("reference", "r2", "read", 0, 0, parsed=False),
    ]
    runs = [
        run_record("official", "o1"),
        run_record("official", "o2", observed_calls=2),
        run_record("reference", "r1"),
        run_record("reference", "r2", unlinked=True),
    ]
    result = argument.summarize(rows, runs)
    assert set(result) == {"official", "r1", "r2", "directories"}
    official = result["official"]
    assert official["read"]["argument_id_grounded_rate"] == {"n": 2, "d": 3}
    assert official["read"]["ungrounded_call_rate"] == {"n": 1, "d": 2}
    assert official["write"]["argument_id_grounded_rate"] == {"n": 1, "d": 1}
    assert official["write_by_ok"]["false"]["argument_id_grounded_rate"] == {
        "n": 0,
        "d": 0,
    }
    assert official["total_reference"]["ids"] == 4
    assert official["counters"]["calls_without_id"] == 1
    assert official["call_count_difference"] == 2 - 3
    assert official["rows_with_call_count_difference"] == 1
    assert result["r2"]["counters"]["arguments_parse_failed"] == 1
    assert result["r2"]["counters"]["unlinked_rows"] == 1
    assert result["r2"]["write_by_ok"]["false"]["argument_id_grounded_rate"] == {
        "n": 1,
        "d": 2,
    }
    assert set(result["directories"]) == {"o1", "o2", "r1", "r2"}


def test_group_without_any_id_stops_with_a_fixed_code():
    with pytest.raises(argument._Failure, match="m8_args_denominator_zero"):
        argument.summarize(
            [row("official", "o1", "read", 0, 0)], [run_record("official", "o1")]
        )


# ---- CLI -----------------------------------------------------------------


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
        "docs: register synthetic argument grounding",
    )
    return root


def make_run(tmp_path, name, tasks, *, thread_for):
    directory = tmp_path / name
    (directory / "attempts").mkdir(parents=True)
    rows = []
    for index, task in enumerate(tasks):
        trace = f"{index + 1:032x}"
        rows.append(
            observation(
                task,
                [
                    {"name": "search_docs", "ok": True},
                    {"name": "jira_add_comment", "ok": True},
                ],
            ).model_copy(update={"trace_id": trace})
        )
        attempt = directory / "attempts" / f"{task}-1"
        (attempt / "audit").mkdir(parents=True)
        (attempt / "attempt.json").write_text(json.dumps({"trace_id": trace}))
        (attempt / "audit/chain-synthetic.jsonl").write_text(
            json.dumps({"task_id": thread_for(task, trace)}) + "\n"
        )
    (directory / "observations.jsonl").write_text(
        "".join(r.model_dump_json() + "\n" for r in rows)
    )
    return directory


class FakeSaver:
    def __init__(self, checkpoints, failing=()):
        self.checkpoints = checkpoints
        self.failing = failing

    async def aget_tuple(self, config):
        thread = config["configurable"]["thread_id"]
        if thread in self.failing:
            raise RuntimeError(f"{PRIVATE} backend detail")
        value = self.checkpoints.get(thread)
        return SimpleNamespace(checkpoint=value) if value is not None else None


def factory(saver, calls=None):
    @asynccontextmanager
    async def opened(url):
        if calls is not None:
            calls.append(url)
        yield saver

    return opened


@pytest.fixture
def world(tmp_path, repo, monkeypatch):
    tasks = ["T1-01", "T1-02"]
    specs_path = tmp_path / "tasks.json"
    specs_path.write_text(
        json.dumps(
            [
                spec(task_id=task)
                .model_copy(update={"request": f"{DOC}?"})
                .model_dump()
                for task in tasks
            ]
        )
    )
    thread = {task: f"{index + 1:032x}" for index, task in enumerate(tasks)}
    official = make_run(tmp_path, "official-1", tasks, thread_for=lambda t, tr: tr)
    reference = make_run(tmp_path, "reference-1", tasks, thread_for=lambda t, tr: tr)
    messages = [
        assistant(
            call("a", "search_docs", {"query": f"{PRIVATE} {DOC}"}),
        ),
        tool("a", OTHER_DOC),
        assistant(
            call("b", "jira_add_comment", {"body": f"{PRIVATE} {OTHER_DOC} {INCIDENT}"})
        ),
    ]
    checkpoints = {t: checkpoint(messages) for t in thread.values()}
    monkeypatch.setenv("SYN_DB_URL", "postgresql://synthetic:pw@localhost:5439/old")
    return SimpleNamespace(
        tmp=tmp_path,
        repo=repo,
        specs=specs_path,
        official=official,
        reference=reference,
        checkpoints=checkpoints,
        args=[
            "--official",
            str(official),
            "--reference",
            str(reference),
            "--specs",
            str(specs_path),
            "--out",
            str(tmp_path / "out"),
            "--url-env",
            "SYN_DB_URL",
            "--dbname",
            "govops_m8_args",
        ],
    )


def run_main(world, args=None, saver=None, calls=None):
    return argument.main(
        args if args is not None else world.args,
        repo_root=world.repo,
        saver_factory=factory(saver or FakeSaver(world.checkpoints), calls),
    )


def test_cli_roundtrip_aggregate_matches_rows_and_hides_arguments(world, capsys):
    calls = []
    assert run_main(world, calls=calls) == 0
    assert calls == ["postgresql://synthetic:pw@localhost:5439/govops_m8_args"]
    out = world.tmp / "out"
    assert sorted(p.name for p in out.iterdir()) == [
        "report.json",
        "report.md",
        "rows.jsonl",
    ]
    report = json.loads((out / "report.json").read_text())
    rows = [json.loads(line) for line in (out / "rows.jsonl").read_text().splitlines()]
    assert report["scope"] == "m8" and report["label"].startswith("M8 추가 측정")
    assert report["registration_commit"] == git(world.repo, "rev-parse", "HEAD")
    recomputed = argument.summarize(rows, report["runs"])
    assert report["directories"] == recomputed.pop("directories")
    assert report["groups"] == recomputed
    official = report["groups"]["official"]
    assert official["read"]["argument_id_grounded_rate"] == {"n": 2, "d": 2}
    assert official["write"]["argument_id_grounded_rate"] == {"n": 2, "d": 4}
    assert official["write_by_ok"]["true"]["ids"] == 4
    assert official["observed_calls"] == official["assistant_calls"] == 4
    assert set(report["groups"]) == {"official", "reference-1"}
    for path in out.iterdir():
        assert PRIVATE not in path.read_text()
    assert set(rows[0]) == {
        "group",
        "result_dir",
        "task_id",
        "run_index",
        "call_index",
        "tool",
        "kind",
        "ok",
        "arguments_ok",
        "ids",
        "grounded",
    }
    blocks, current = [], []
    for line in (out / "report.md").read_text().splitlines():
        if line.startswith("|"):
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    assert len(blocks) == 3
    for block in blocks:
        assert len({len(line.split("|")) for line in block}) == 1
        for line in block:
            assert all(cell.strip() for cell in line.split("|")[1:-1])
    assert PRIVATE not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda w: (w.repo / REGISTRATION).write_text("changed\n"),
            "m8_args_registration_not_committed",
        ),
        (lambda w: (w.tmp / "out").mkdir(), "m8_args_out_exists"),
    ],
)
def test_cli_stops_with_fixed_codes_before_reading_the_database(
    world, capsys, mutate, code
):
    mutate(world)
    calls = []
    assert run_main(world, calls=calls) == 1
    assert capsys.readouterr().out.strip() == f"FAIL {code}"
    assert calls == []


def test_cli_input_and_environment_codes(world, capsys, monkeypatch):
    def code():
        assert run_main(world) == 1
        return capsys.readouterr().out.strip().removeprefix("FAIL ")

    inside = world.args[:]
    inside[inside.index("--out") + 1] = str(world.official / "inside")
    assert run_main(world, inside) == 1
    assert capsys.readouterr().out.strip() == "FAIL m8_args_out_overlaps_input"
    assert run_main(world, ["--official", str(world.official), "--bad"]) == 1
    assert capsys.readouterr().out.strip() == "FAIL m8_args_input_invalid"
    assert run_main(world, []) == 1
    assert capsys.readouterr().out.strip() == "FAIL m8_args_input_invalid"
    monkeypatch.setenv("SYN_DB_URL", "  ")
    assert code() == "m8_args_url_env_unset"
    monkeypatch.delenv("SYN_DB_URL")
    assert code() == "m8_args_url_env_unset"
    monkeypatch.setenv("SYN_DB_URL", "postgresql://synthetic:pw@localhost/old")
    bad_db = world.args[:]
    bad_db[bad_db.index("--dbname") + 1] = "Bad-Name"
    assert run_main(world, bad_db) == 1
    assert capsys.readouterr().out.strip() == "FAIL m8_args_input_invalid"
    twice = world.args[:]
    twice[twice.index("--reference") + 1] = str(world.official)
    assert run_main(world, twice) == 1
    assert capsys.readouterr().out.strip() == "FAIL m8_args_duplicate_run"
    world.specs.write_text("[]")
    assert code() == "m8_args_unknown_task"
    world.specs.write_text("not json")
    assert code() == "m8_args_input_invalid"
    world.specs.unlink()
    assert code() == "m8_args_input_missing"


def test_cli_database_errors_never_leak_backend_text(world, capsys):
    failing = FakeSaver(world.checkpoints, failing=(next(iter(world.checkpoints)),))
    assert run_main(world, saver=failing) == 1
    output = capsys.readouterr().out
    assert output.strip() == "FAIL m8_args_thread_read_failed"
    assert PRIVATE not in output

    @asynccontextmanager
    async def broken(url):
        raise RuntimeError(f"{PRIVATE} postgresql://synthetic:pw@host/db")
        yield

    assert argument.main(world.args, repo_root=world.repo, saver_factory=broken) == 1
    output = capsys.readouterr().out
    assert output.strip() == "FAIL m8_args_db_connect_failed"
    assert "pw@host" not in output and PRIVATE not in output


def test_cli_checkpoint_shape_and_all_ids_missing(world, capsys):
    bad = {thread: "not a checkpoint" for thread in world.checkpoints}
    assert run_main(world, saver=FakeSaver(bad)) == 1
    assert capsys.readouterr().out.strip() == "FAIL m8_args_checkpoint_shape"
    quiet = {
        thread: checkpoint([assistant(call("a", "search_docs", {"query": "ID 없음"}))])
        for thread in world.checkpoints
    }
    assert run_main(world, saver=FakeSaver(quiet)) == 1
    assert capsys.readouterr().out.strip() == "FAIL m8_args_denominator_zero"


def test_missing_threads_are_counted_not_dropped(world):
    first = min(world.checkpoints)
    partial = {first: world.checkpoints[first]}
    assert run_main(world, saver=FakeSaver(partial)) == 0
    report = json.loads((world.tmp / "out/report.json").read_text())
    counters = report["groups"]["official"]["counters"]
    assert counters["checkpoint_missing"] == 1
    assert counters["rows_without_calls"] == 1
    assert report["groups"]["official"]["rows"] == 2
    assert report["thread_count"] == 2


# ---- registration document, Makefile ----------------------------------------


def test_registration_document_matches_cli_codes_and_example_arguments():
    text = (ROOT / REGISTRATION).read_text(encoding="utf-8")
    section = text.split("## 8. 고정 코드 목록", 1)[1].split("## 9.", 1)[0]
    assert set(re.findall(r"m8_args_[a-z_]+", section)) == set(argument.CODES)
    counters = set(re.findall(r"`([a-z_]+)`", section.split("보고 카운터:")[1]))
    assert counters == set(argument.COUNTERS)
    examples = re.findall(r'make m8-args ARGUMENT_ARGS="([^"]*)"', text)
    assert examples
    for example in examples:
        parsed = argument.build_parser().parse_args(shlex.split(example))
        assert (
            parsed.official and parsed.reference and parsed.dbname == "govops_m8_args"
        )
    assert argument._REGISTRATION == REGISTRATION


def test_make_target_uses_env_file_and_is_outside_verify():
    text = (ROOT / "Makefile").read_text()
    recipe = text.split("\nm8-args:\n", 1)[1].split("\n\n", 1)[0]
    assert "--env-file .env" in recipe and "$(ARGUMENT_ARGS)" in recipe
    assert "govops.grounding.argument" in recipe
    verify = text.split("verify:\n", 1)[1].split("\n\n", 1)[0]
    assert "m8-args" not in verify
