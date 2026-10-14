"""M6 runner paths using only synthetic specs, files and services."""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from govops.eval.spec import TaskSpec
from govops.evalrun import dev_m3, run_guards

ROOT = Path(__file__).resolve().parents[1]


def spec(index=1, *, split="dev", task_id=None):
    return TaskSpec.model_validate(
        {
            "task_id": task_id or f"T1-{index:02}",
            "type": "T1",
            "subtype": "summary",
            "split": split,
            "executable_in": "m2" if index % 2 else "m3",
            "user": "synthetic-user",
            "request": f"합성 연습 서비스 {index}를 한 문장으로 설명해 주세요.",
            "expected": {"outcome": "answer", "effects": []},
            "required_tools_any": [],
            "forbidden": {
                "leak_doc_ids": [],
                "leak_ticket_ids": [],
                "leak_values": [],
                "forbidden_tools": [],
            },
            "required_facts": [],
            "derived_values": [],
            "approval_script": [],
            "provenance": {
                "meta_sha256": "0" * 64,
                "source_doc_ids": [],
                "source_ticket_ids": [],
            },
            "spec_sha256": "0" * 64,
        }
    )


def rows():
    return [spec(i) for i in range(1, 21)] + [
        spec(i, split="test") for i in range(21, 61)
    ]


def test_default_and_test_selection():
    corpus = rows()
    assert dev_m3.select_tasks(corpus) == corpus[:20]
    assert dev_m3.select_tasks(corpus, [s.task_id for s in corpus[:20]]) == corpus[:20]
    assert dev_m3.select_tasks(corpus, split="test") == corpus[20:]
    assert len(dev_m3.select_tasks(corpus, split="test")) == 40
    assert (
        dev_m3.select_tasks(corpus, [s.task_id for s in corpus[20:]], split="test")
        == corpus[20:]
    )
    with pytest.raises(ValueError, match="^partial_test_refused$"):
        dev_m3.select_tasks(corpus, [corpus[20].task_id], split="test")
    with pytest.raises(ValueError, match="^partial_test_refused$"):
        dev_m3.select_tasks(corpus, [corpus[20].task_id], split="test", smoke=True)
    with pytest.raises(ValueError, match="^test_task_refused$"):
        dev_m3.select_tasks(corpus, [corpus[0].task_id, corpus[20].task_id])
    with pytest.raises(ValueError, match="^partial_dev_refused$"):
        dev_m3.select_tasks(corpus, [corpus[0].task_id])


def test_test_execution_requires_opt_in(tmp_path):
    with pytest.raises(ValueError, match="^test_task_refused$"):
        asyncio.run(
            dev_m3.run_dev_m3(
                [spec(split="test")],
                root=tmp_path,
                chat=None,
                tools=None,
                mock_admin=None,
                seed_check=None,
                metadata={},
                costs=dict,
                preflight=list,
            )
        )


@dataclass
class Tcr:
    passed: bool = True


@dataclass
class Grade:
    tcr: Tcr


def run_fake(tmp_path, monkeypatch, *, chosen=None, budget=3, counts=None, **options):
    monkeypatch.setattr(dev_m3, "guard_live_m3", lambda _: None)
    monkeypatch.setattr(dev_m3, "guard_grading_rules", lambda *a: {})
    monkeypatch.setattr(dev_m3, "load_meta", lambda path: {})
    monkeypatch.setattr(dev_m3, "load_users", lambda: {"synthetic-user": object()})
    monkeypatch.setattr(dev_m3, "requester_section", lambda _: "합성 요청자")
    monkeypatch.setattr(dev_m3, "exploration_budget_from_env", lambda: budget)
    monkeypatch.setattr(dev_m3, "grade_run", lambda **kw: Grade(Tcr()))
    monkeypatch.setattr(dev_m3, "rules_sha256_full", lambda _: "a" * 64)
    monkeypatch.setattr(
        dev_m3.worktree, "ledger_state", lambda _: {"lines": 0, "sha256": None}
    )
    for name in ("tickets", "users"):
        path = tmp_path / f"data/seed/{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({name: []}))

    def forbidden_recording(*args, **kwargs):
        pytest.fail("golden recorder must not be constructed")

    monkeypatch.setattr(dev_m3, "RecordingChatClient", forbidden_recording)
    monkeypatch.setattr(dev_m3, "RecordingToolExecutor", forbidden_recording)

    class Service:
        def __init__(self, **kwargs):
            self.graph = SimpleNamespace(agent_profile=dev_m3.PROFILE)
            self.users = {}
            self.audit = None

        async def aclose(self):
            pass

    class Driver:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, task):
            return SimpleNamespace(
                task_id="a" * 32,
                view={"status": "completed", "answer": "합成 답변"},
                approvals=[],
                unexpected_http=False,
                timed_out=False,
                plan_remaining=0,
            )

    async def get_task(*args):
        return {"messages": []}

    monkeypatch.setattr(dev_m3, "ApprovalDriver", Driver)
    monkeypatch.setattr(dev_m3, "get_task", get_task)
    return asyncio.run(
        dev_m3.run_dev_m3(
            chosen or [spec()],
            root=tmp_path,
            evaluation_root=tmp_path,
            chat=SimpleNamespace(
                status_counts=lambda: counts or {}, config_sha256=lambda: "b" * 64
            ),
            tools=SimpleNamespace(jira=object(), slack=object(), policy=object()),
            mock_admin=SimpleNamespace(reset=lambda: None, state=lambda **kw: {}),
            seed_check=lambda: True,
            metadata={"a_git_head": "d2eec9c", "guardrail_rules_sha256": "a" * 64},
            costs=dict,
            preflight=list,
            saver=object(),
            outbox=object(),
            service_factory=Service,
            **options,
        )
    )


def hashes(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("existing", [False, True])
def test_test_run_never_creates_or_changes_golden(tmp_path, monkeypatch, existing):
    replay = tmp_path / "data/replay"
    if existing:
        for name, content in (
            ("golden/status.json", "{}"),
            ("llm/fixture.jsonl", "합성"),
            ("a_run/fixture.jsonl", "합성"),
        ):
            path = replay / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    before = hashes(replay)
    result = run_fake(
        tmp_path, monkeypatch, chosen=[spec(split="test")], allow_test=True
    )
    assert Path(result["directory"]).parent == tmp_path / "data/results/test"
    assert hashes(replay) == before
    assert not list(Path(result["directory"]).rglob("llm"))
    assert not list(Path(result["directory"]).rglob("a_run"))


def test_allow_test_refuses_mixed_splits(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="^test_task_refused$"):
        run_fake(
            tmp_path,
            monkeypatch,
            chosen=[spec(), spec(2, split="test")],
            allow_test=True,
        )
    assert not (tmp_path / "data/results").exists()
    with pytest.raises(ValueError, match="^test_task_refused$"):
        run_fake(
            tmp_path,
            monkeypatch,
            chosen=dev_m3.smoke_specs(),
            allow_test=True,
            smoke=True,
        )


# Frozen pre-M6 manifest keys, excluding only the explicitly injected metadata.
LEGACY_KEYS = {
    "a_git_head",
    "guardrail_rules_sha256",
    "requester_section_sha256",
    "ledger_at_start",
    "exploration_budget",
    "run_role",
    "stop_reasons",
    "mode",
    "grading_rules",
    "profile",
    "model_id",
    "call_params",
    "prompt_hash",
    "contract_snapshot_sha256",
    "started_at",
    "guardrail_v2",
    "finished_at",
    "models_seen",
    "unmapped_models",
    "fallback_used",
    "models_by_task",
    "mixed_model_tasks",
    "mixed_model_tcr",
    "trace_ids",
    "tool_trace_mismatch",
    "attempts",
    "infrastructure_failures",
    "costs_by_purpose",
    "absolute_rule_violations",
    "effect_counts",
    "warnings",
    "n_registration_blocked",
    "n_registration_mismatch_tasks",
    "late_tool_calls",
    "guardrail",
}


def test_no_record_dev_and_legacy_manifest_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVOPS_GUARDRAIL_V2", "off")
    result = run_fake(tmp_path, monkeypatch, record_golden=False)
    assert spec().task_id in dev_m3.golden.GOLDEN_TASKS
    assert Path(result["directory"]).parent == tmp_path / "data/results/dev"
    assert not (tmp_path / "data/replay").exists()
    assert set(result["manifest"]) == LEGACY_KEYS


@pytest.mark.parametrize("budget,role", [(3, "official"), (None, "reference")])
@pytest.mark.parametrize(
    "switch,counts,validity",
    [("on", {}, "invalid_v2_off"), ("on", {"ok": 20}, "valid"), ("off", {}, "valid")],
)
def test_m6_manifest_retains_exploration_role(
    tmp_path, monkeypatch, budget, role, switch, counts, validity
):
    monkeypatch.setenv("GOVOPS_GUARDRAIL_V2", switch)
    config = "v1+v2" if switch == "on" else "v1"
    result = run_fake(
        tmp_path,
        monkeypatch,
        record_golden=False,
        run_label="N1b",
        config=config,
        budget=budget,
        counts=counts,
    )
    manifest = result["manifest"]
    assert set(manifest) == LEGACY_KEYS | {
        "run_label",
        "config",
        "m6_run",
        "v2_validity",
        "validity",
    }
    assert manifest["run_label"] == manifest["m6_run"] == "N1b"
    assert manifest["config"] == config
    assert manifest["v2_validity"] == run_guards.v2_validity(switch, counts)
    assert manifest["validity"] == validity
    assert manifest["run_role"] == role


@pytest.mark.parametrize(
    "arguments",
    [
        ["--split", "dev"],
        ["--config", "v1"],
        ["--run", "N1a"],
        ["--repeat-index", "1"],
        ["--no-record-golden"],
    ],
)
def test_new_arguments_require_m6(monkeypatch, capsys, arguments):
    async def live(*args, **kwargs):
        pytest.fail("live must not run")

    monkeypatch.setattr(dev_m3, "_live", live)
    assert dev_m3.main(arguments) == 1
    assert capsys.readouterr().out.strip() == "invalid_arguments"


def cli_fakes(tmp_path, monkeypatch, *, fail=None, counts=None, switch="off"):
    monkeypatch.setattr(dev_m3, "ROOT", tmp_path)
    monkeypatch.setenv("GOVOPS_GUARDRAIL_V2", switch)
    monkeypatch.setattr(dev_m3.LiteLLMChatClient, "status_counts", lambda: counts or {})
    events = []
    originals = {
        name: getattr(run_guards, name)
        for name in ("guard_switch", "guard_fresh_status_counts")
    }
    for name in (
        "guard_switch",
        "guard_fresh_status_counts",
        "guard_redteam_registration",
        "guard_clean_worktree",
        "guard_test_targets",
    ):

        def guard(*args, _name=name, **kwargs):
            events.append((_name, args, kwargs))
            if _name in originals:
                return originals[_name](*args, **kwargs)
            if fail == _name:
                raise ValueError("hypotheses_test_targets_not_committed")
            if (
                _name == "guard_test_targets"
                and kwargs["role"] == "official"
                and kwargs["config"] != "v1+v2"
            ):
                raise ValueError("official_config_mismatch")

        monkeypatch.setattr(run_guards, name, guard)
    path = tmp_path / "data/eval/tasks.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps([s.model_dump() for s in rows()]))
    monkeypatch.setattr(dev_m3, "git_metadata_m3", lambda *a: {})

    async def live(*args, **kwargs):
        events.append(("_live", args, kwargs))
        return {}

    monkeypatch.setattr(dev_m3, "_live", live)
    return events


@pytest.mark.parametrize(
    "run,fail,counts,switch,code,order",
    [
        (
            "N2",
            "guard_test_targets",
            {},
            "off",
            "hypotheses_test_targets_not_committed",
            5,
        ),
        ("N2", None, {}, "off", "official_config_mismatch", 5),
        ("N3", None, {}, "on", "guardrail_v2_switch_mismatch", 1),
        ("N3", None, {"ok": 1}, "off", "status_counts_not_fresh", 2),
    ],
)
def test_cli_guards_stop_before_live(
    tmp_path, monkeypatch, capsys, run, fail, counts, switch, code, order
):
    events = cli_fakes(tmp_path, monkeypatch, fail=fail, counts=counts, switch=switch)
    assert (
        dev_m3.main(
            [
                "--m6",
                "--split",
                "test",
                "--config",
                "v1",
                "--run",
                run,
                "--repeat-index",
                "1",
            ]
        )
        == 1
    )
    assert capsys.readouterr().out.strip() == code
    names = [e[0] for e in events]
    assert (
        names
        == [
            "guard_switch",
            "guard_fresh_status_counts",
            "guard_redteam_registration",
            "guard_clean_worktree",
            "guard_test_targets",
        ][:order]
    )


def test_cli_reference_and_legacy_dispatch(tmp_path, monkeypatch):
    events = cli_fakes(tmp_path, monkeypatch)
    assert (
        dev_m3.main(
            [
                "--m6",
                "--split",
                "test",
                "--config",
                "v1",
                "--run",
                "N3",
                "--repeat-index",
                "1",
                "--no-record-golden",
            ]
        )
        == 0
    )
    assert events[-2][2] == {"config": "v1", "role": "reference"}
    assert len(events[-1][1][0]) == 40
    assert events[-1][2] == {
        "smoke": False,
        "allow_test": True,
        "record_golden": False,
        "run_label": "N3",
        "config": "v1",
    }
    assert events[-1][1][1]["repeat_index"] == 1
    events.clear()
    assert dev_m3.main([]) == 0
    assert [e[0] for e in events] == ["_live"]
    assert len(events[-1][1][0]) == 20
    assert events[-1][2] == {"smoke": False}
    assert events[-1][1][1] == {}


@pytest.mark.parametrize(
    "extra", [["--repeat-index", "0"], ["--repeat-index", "4"], ["--smoke"]]
)
def test_invalid_m6_arguments(tmp_path, monkeypatch, capsys, extra):
    events = cli_fakes(tmp_path, monkeypatch)
    assert dev_m3.main(["--m6", "--config", "v1", "--run", "N1a", *extra]) == 1
    assert capsys.readouterr().out.strip() == "invalid_arguments"
    assert not events


def test_makefile_actual_runner_wiring():
    text = (ROOT / "Makefile").read_text()
    for target, args in {
        "eval-run-dev": "",
        "eval-run-dev-m6": " --m6 --split dev --config $(CONFIG) --run $(RUN) --no-record-golden",
        "eval-run-test": " --m6 --split test --config $(CONFIG) --run $(RUN) --repeat-index $(REPEAT) --no-record-golden",
    }.items():
        recipe = text.split(f"\n{target}:\n", 1)[1].splitlines()[0]
        assert (
            recipe == "\tuv run --env-file .env python -m govops.evalrun.dev_m3" + args
        )
        assert target in {
            word
            for line in text.splitlines()
            if line.startswith(".PHONY:")
            for word in line.split()[1:]
        }
        if target != "eval-run-dev":
            assert text.split(f"\n{target}:\n")[0].splitlines()[-1] == "# 사용자 실행"
            assert text.index(f"\n{target}:\n") < text.index("\ntest-outbox-pg:\n")


def test_live_assembly_forwards_m6_options_without_connecting(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVOPS_DATABASE_URL", "synthetic-database")
    monkeypatch.setattr(dev_m3, "ROOT", tmp_path)
    monkeypatch.setattr(dev_m3, "register_secret_values", lambda _: None)
    monkeypatch.setattr(dev_m3, "MockAdmin", lambda _: object())
    monkeypatch.setattr(dev_m3, "AMcpClient", lambda _: object())
    monkeypatch.setattr(dev_m3, "run_preflight", lambda **kw: [])
    monkeypatch.setattr(
        dev_m3,
        "load_users",
        lambda: {"synthetic-user": SimpleNamespace(team="platform")},
    )
    monkeypatch.setattr(
        dev_m3,
        "TeamKeyResolver",
        lambda _: SimpleNamespace(missing=lambda: False, key_for=lambda _: None),
    )
    monkeypatch.setattr(dev_m3, "CostLedger", lambda: object())
    monkeypatch.setattr(dev_m3, "compute_digests", lambda _: {})
    monkeypatch.setattr(dev_m3, "LiteLLMChatClient", lambda **kw: object())
    for name in ("ToolRuntime", "JiraClient", "SlackClient", "PostgresOutboxStore"):
        monkeypatch.setattr(dev_m3, name, lambda *a, **kw: object())

    @asynccontextmanager
    async def context(*args, **kwargs):
        yield object()

    monkeypatch.setattr(dev_m3, "open_checkpointer", context)
    monkeypatch.setattr(dev_m3.httpx, "AsyncClient", context)
    calls = []

    async def runner(specs, **kwargs):
        calls.append((specs, kwargs))
        return {"synthetic": True}

    monkeypatch.setattr(dev_m3, "run_dev_m3", runner)
    options = {
        "allow_test": True,
        "record_golden": False,
        "run_label": "N2",
        "config": "v1+v2",
    }
    chosen = [spec(split="test")]
    assert asyncio.run(dev_m3._live(chosen, {}, **options)) == {"synthetic": True}
    assert len(calls) == 1 and calls[0][0] == chosen
    assert {name: calls[0][1][name] for name in options} == options
