"""Graph execution through automatic forbidden lists to both offline graders."""

import asyncio
import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, call, reply
from test_eval_taskgen import fixture, generated

from govops.agent.graph import build_graph, run_task
from govops.eval.forbidden_auto import auto_forbidden
from govops.eval.meta import load_meta
from govops.eval.rules import summarize_rules, unreadable_ids_for
from govops.eval.run_grade import build_observation, grade_run
from govops.eval.spec import TaskSpec, spec_hash
from govops.eval.visibility import can_read
from govops.tools.registry import ToolOutcome

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def corpus():
    paths = [
        ROOT / p
        for p in (
            "data/eval/meta.json",
            "data/eval/tasks.json",
            "data/seed/tickets.json",
            "data/seed/users.json",
        )
    ]
    if all(p.is_file() for p in paths):
        meta = load_meta(paths[0])
        specs = [TaskSpec.model_validate(s) for s in json.loads(paths[1].read_text())]
        tickets = json.loads(paths[2].read_text())["tickets"]
        users = json.loads(paths[3].read_text())["users"]
    else:
        meta, tickets, users = fixture()
        specs = generated(True)[0]
    spec = next(s for s in specs if s.executable_in == "m2")
    return spec, meta, tickets, users


def execute(spec, answer, observed=(), *, failed=False, ok=True):
    class Tools:
        def __init__(self):
            self.calls = []

        async def call(self, name, arguments, *, user_id):
            assert user_id == spec.user
            self.calls.append({"name": name, "ok": ok})
            return ToolOutcome({}, not ok, tuple(observed))

    async def run():
        tools = Tools()
        chat = FakeChat(
            reply(calls=[call()]), RuntimeError("fake") if failed else reply(answer)
        )
        state = await run_task(
            build_graph(chat, tools, InMemorySaver()),
            task_id=spec.task_id,
            user_id=spec.user,
            request=spec.request,
        )
        obs = build_observation(
            spec=spec,
            run_index=0,
            state=state,
            tool_calls=tools.calls,
            trace_id="synthetic-trace",
        )
        assert obs.answer == state["answer"]
        assert obs.observed_ids == state["observed_ids"]
        assert obs.cited_doc_ids == state["cited_doc_ids"]
        assert obs.effects == obs.approvals == obs.effect_bodies == []
        assert obs.tool_calls[0].ok == ok
        return state, obs

    return asyncio.run(run())


@pytest.mark.parametrize("kind", ["doc", "ticket", "literal", "canary"])
def test_missing_forbidden_items_flow_to_both_graders(corpus, kind):
    spec, meta, tickets, users = corpus
    auto = auto_forbidden(spec, meta, tickets, users, grading_rules="registered")
    user = next(u for u in users if u["label"] == spec.user)
    if kind == "doc":
        value = next(
            v for v in auto.leak_doc_ids if v not in spec.forbidden.leak_doc_ids
        )
    elif kind == "ticket":
        value = next(
            v for v in auto.leak_ticket_ids if v not in spec.forbidden.leak_ticket_ids
        )
    else:
        pool = (
            {c.value for c in meta.canaries}
            if kind == "canary"
            else {
                f.value
                for f in meta.facts
                if f.kind in {"id", "service", "datetime"} and f.source_kind != "canary"
            }
        )
        value = next(
            v
            for v in auto.leak_values
            if v in pool and v not in spec.forbidden.leak_values
        )
    before = spec.model_dump_json()
    _, obs = execute(spec, value)
    grade = grade_run(
        spec=spec,
        obs=obs,
        meta=meta,
        tickets=tickets,
        users=users,
        grading_rules="registered",
    )
    assert grade.used_auto_forbidden is True
    assert not grade.tcr.passed and "forbidden_leak" in grade.tcr.reasons
    assert any("answer" in c.locations for c in grade.rules.leak_candidates)
    assert grade.rules.leak_pending == grade.rules.leak_candidate_count > 0
    assert grade.rules.leak_confirmed == 0
    assert summarize_rules([grade.rules]).verdict == "pending_review"
    assert spec.model_dump_json() == before
    assert spec_hash(spec) == spec.spec_sha256
    assert unreadable_ids_for(user, meta, tickets, grading_rules="registered")
    confirmations = {
        c.candidate_id: "false_positive" for c in grade.rules.leak_candidates
    }
    reviewed = grade_run(
        spec=spec,
        obs=obs,
        meta=meta,
        tickets=tickets,
        users=users,
        confirmations=confirmations,
        grading_rules="registered",
    )
    assert reviewed.rules.leak_pending == 0
    assert reviewed.used_auto_forbidden is True


def test_readable_ids_values_and_shared_values_are_not_candidates(corpus):
    spec, meta, tickets, users = corpus
    user = next(u for u in users if u["label"] == spec.user)
    doc = next(
        d
        for d in meta.docs
        if can_read(user, d.access_level, d.access_teams, d.access_min_grade)
        and d.doc_id not in spec.forbidden.leak_doc_ids
    )
    hidden_value = next(
        v
        for v in auto_forbidden(
            spec, meta, tickets, users, grading_rules="registered"
        ).leak_values
        if v not in spec.forbidden.leak_values
    )
    fact = next(f for f in meta.facts if f.value == hidden_value)
    shared_doc = doc.model_copy(update={"fact_ids": [*doc.fact_ids, fact.fact_id]})
    meta = meta.model_copy(
        update={
            "docs": [shared_doc if d.doc_id == doc.doc_id else d for d in meta.docs]
        }
    )
    value = next(
        meta.fact_by_id[f].value
        for f in doc.fact_ids
        if meta.fact_by_id[f].value not in spec.forbidden.leak_values
    )
    _, obs = execute(spec, f"[{doc.doc_id}] {value} {hidden_value}", [doc.doc_id])
    grade = grade_run(
        spec=spec,
        obs=obs,
        meta=meta,
        tickets=tickets,
        users=users,
        grading_rules="registered",
    )
    assert grade.used_auto_forbidden is True
    assert "forbidden_leak" not in grade.tcr.reasons
    assert grade.rules.leak_candidate_count == 0


def test_observed_unreadable_id_also_uses_rule_oracle(corpus):
    spec, meta, tickets, users = corpus
    value = next(
        v
        for v in auto_forbidden(
            spec, meta, tickets, users, grading_rules="registered"
        ).leak_doc_ids
        if v not in spec.forbidden.leak_doc_ids
    )
    _, obs = execute(spec, "조회 종료", [value])
    grade = grade_run(
        spec=spec,
        obs=obs,
        meta=meta,
        tickets=tickets,
        users=users,
        grading_rules="registered",
    )
    assert "forbidden_leak" in grade.tcr.reasons
    assert any("observed_ids" in c.locations for c in grade.rules.leak_candidates)


def test_failed_state_and_identity_mismatch(corpus):
    spec, _, _, _ = corpus
    state, obs = execute(spec, "", failed=True, ok=False)
    assert obs.status == "failed" and "error" not in obs.model_dump()
    with pytest.raises(ValueError, match="user_id_mismatch"):
        build_observation(
            spec=spec,
            run_index=0,
            state={**state, "user_id": "demo-other"},
            tool_calls=[],
            trace_id=None,
        )
    with pytest.raises(ValueError, match="invalid_status"):
        build_observation(
            spec=spec,
            run_index=0,
            state={**state, "status": "running"},
            tool_calls=[],
            trace_id=None,
        )


@pytest.mark.parametrize(
    "rules,passed,count", [("registered", False, 1), ("m3-grading", True, 0)]
)
def test_saved_t2_grading_version(rules, passed, count):
    from types import SimpleNamespace

    from test_regolden import grading_data, observations

    from govops.golden import load_golden_specs, record_status, tcr_status

    spec = load_golden_specs()["T2-01"]
    obs = observations()["T2-01"]
    data = grading_data()
    grade = grade_run(spec=spec, obs=obs, **data, grading_rules=rules)
    assert grade.tcr.passed is passed
    assert grade.rules.leak_candidate_count == count
    expected = "pass" if passed else "recorded_fail"
    assert tcr_status(spec, obs, **data, grading_rules=rules) == expected
    assert (
        record_status(
            spec,
            obs,
            SimpleNamespace(golden_eligible=True),
            **data,
            grading_rules=rules,
        )["status"]
        == expected
    )


def test_registered_saved_twenty_unchanged():
    from dataclasses import asdict

    from test_regolden import RUN, grading_data, observations

    specs = {
        s.task_id: s
        for s in map(
            TaskSpec.model_validate,
            json.loads((ROOT / "data/eval/tasks.json").read_text()),
        )
    }
    saved = json.loads((RUN / "grades.json").read_text())
    passes = 0
    for key, obs in observations().items():
        result = grade_run(
            spec=specs[key], obs=obs, **grading_data(), grading_rules="registered"
        )
        assert asdict(result.tcr) == {
            **saved[key]["tcr"],
            "reasons": tuple(saved[key]["tcr"]["reasons"]),
        }
        assert result.rules.leak_candidate_count == len(
            saved[key]["rules"]["leak_candidates"]
        )
        passes += result.tcr.passed
    assert passes == 17


def test_grading_rules_required_and_valid(corpus):
    spec, meta, tickets, users = corpus
    _, obs = execute(spec, "")
    kwargs = {
        "spec": spec,
        "obs": obs,
        "meta": meta,
        "tickets": tickets,
        "users": users,
    }
    with pytest.raises(TypeError):
        grade_run(**kwargs)
    with pytest.raises(ValueError):
        grade_run(**kwargs, grading_rules="unknown")


@pytest.mark.parametrize("case", ["uncommitted", "changed", "dirty", "clean"])
def test_grading_registration_guard(tmp_path, case):
    import subprocess

    from govops.eval.run_grade import guard_grading_rules

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init")
    git("config", "user.email", "synthetic@example.invalid")
    git("config", "user.name", "synthetic")
    path = tmp_path / "docs/results/hypotheses-m3-grading.md"
    path.parent.mkdir(parents=True)
    path.write_text("registered rule\n")
    assert guard_grading_rules(tmp_path, "registered") == {}
    if case != "uncommitted":
        git("add", ".")
        git("commit", "-m", "docs: registration")
        registration = git("rev-parse", "HEAD")
        if case == "changed":
            path.write_text("changed rule\n")
            git("commit", "-am", "docs: changed")
        elif case == "dirty":
            path.write_text("dirty rule\n")
    if case == "clean":
        result = guard_grading_rules(tmp_path, "m3-grading")
        assert result["grading_rules_registration_commit"] == registration
        import hashlib

        assert (
            result["grading_rules_sha256"]
            == hashlib.sha256(path.read_bytes()).hexdigest()
        )
    else:
        with pytest.raises(ValueError, match="grading_rules_not_committed"):
            guard_grading_rules(tmp_path, "m3-grading")


def _grading_repo(tmp_path, *, commit_registration=True):
    import subprocess

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init")
    git("config", "user.email", "synthetic@example.invalid")
    git("config", "user.name", "synthetic")
    ledger = tmp_path / "data/manifest/costs.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"row": 1}\n')
    path = tmp_path / "docs/results/hypotheses-m3-grading.md"
    path.parent.mkdir(parents=True)
    path.write_text("registered rule\n")
    git("add", "data")
    if commit_registration:
        git("add", "docs")
    git("commit", "-m", "chore: base")
    return git, path, ledger


def test_grading_guard_ignores_ledger_only_changes(tmp_path):
    from govops.eval.run_grade import guard_grading_rules

    git, _, ledger = _grading_repo(tmp_path)
    registration = git("rev-parse", "HEAD")
    # smoke가 원장에 줄을 더했다(청결 검사 예외 대상). 등록 파일은 그대로다.
    ledger.write_text('{"row": 1}\n{"row": 2}\n')
    assert git("status", "--porcelain")  # 작업 트리 전체는 더럽다
    result = guard_grading_rules(tmp_path, "m3-grading")
    assert result["grading_rules_registration_commit"] == registration


def test_grading_guard_does_not_judge_unrelated_files(tmp_path):
    from govops.eval.run_grade import guard_grading_rules

    _, _, _ = _grading_repo(tmp_path)
    (tmp_path / "unrelated.txt").write_text("x\n")
    # 작업 트리 전체의 청결은 이 가드가 아니라 dev_m3의 dirty_worktree 검사가 맡는다.
    assert guard_grading_rules(tmp_path, "m3-grading")["grading_rules_sha256"]


def test_grading_guard_still_fails_when_registration_file_is_modified(tmp_path):
    from govops.eval.run_grade import guard_grading_rules

    _, path, ledger = _grading_repo(tmp_path)
    ledger.write_text('{"row": 1}\n{"row": 2}\n')  # 원장 변경이 있어도
    path.write_text("changed after registration\n")
    with pytest.raises(ValueError, match="grading_rules_not_committed"):
        guard_grading_rules(tmp_path, "m3-grading")


def test_grading_guard_fails_when_registration_is_not_committed_in_a_repo(tmp_path):
    from govops.eval.run_grade import guard_grading_rules

    # 저장소에 다른 커밋은 있지만 등록 파일은 추적되지 않는다.
    _grading_repo(tmp_path, commit_registration=False)
    with pytest.raises(ValueError, match="grading_rules_not_committed"):
        guard_grading_rules(tmp_path, "m3-grading")


@pytest.mark.parametrize("kind", ["doc", "ticket", "literal", "canary"])
def test_m3_still_rejects_unshared_forbidden_values(corpus, kind):
    from govops.eval.access_index import AccessIndex

    spec, meta, tickets, users = corpus
    user = next(u for u in users if u["label"] == spec.user)
    index = AccessIndex(meta, tickets)
    old = auto_forbidden(spec, meta, tickets, users, grading_rules="registered")
    new = auto_forbidden(spec, meta, tickets, users, grading_rules="m3-grading")
    assert new.leak_values == old.leak_values
    if kind in {"doc", "ticket"}:
        field = "leak_doc_ids" if kind == "doc" else "leak_ticket_ids"
        value = next(v for v in getattr(new, field) if not index.value_visible(user, v))
    else:
        pool = (
            {c.value for c in meta.canaries}
            if kind == "canary"
            else {
                f.value
                for f in meta.facts
                if f.kind in {"id", "service", "datetime"} and f.source_kind != "canary"
            }
        )
        value = next(v for v in new.leak_values if v in pool)
    _, obs = execute(spec, value)
    result = grade_run(
        spec=spec,
        obs=obs,
        meta=meta,
        tickets=tickets,
        users=users,
        grading_rules="m3-grading",
    )
    assert "forbidden_leak" in result.tcr.reasons
    assert result.rules.leak_candidate_count > 0
