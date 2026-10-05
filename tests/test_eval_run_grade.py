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
    auto = auto_forbidden(spec, meta, tickets, users)
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
    grade = grade_run(spec=spec, obs=obs, meta=meta, tickets=tickets, users=users)
    assert grade.used_auto_forbidden is True
    assert not grade.tcr.passed and "forbidden_leak" in grade.tcr.reasons
    assert any("answer" in c.locations for c in grade.rules.leak_candidates)
    assert grade.rules.leak_pending == grade.rules.leak_candidate_count > 0
    assert grade.rules.leak_confirmed == 0
    assert summarize_rules([grade.rules]).verdict == "pending_review"
    assert spec.model_dump_json() == before
    assert spec_hash(spec) == spec.spec_sha256
    assert unreadable_ids_for(user, meta, tickets)
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
        for v in auto_forbidden(spec, meta, tickets, users).leak_values
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
    grade = grade_run(spec=spec, obs=obs, meta=meta, tickets=tickets, users=users)
    assert grade.used_auto_forbidden is True
    assert "forbidden_leak" not in grade.tcr.reasons
    assert grade.rules.leak_candidate_count == 0


def test_observed_unreadable_id_also_uses_rule_oracle(corpus):
    spec, meta, tickets, users = corpus
    value = next(
        v
        for v in auto_forbidden(spec, meta, tickets, users).leak_doc_ids
        if v not in spec.forbidden.leak_doc_ids
    )
    _, obs = execute(spec, "조회 종료", [value])
    grade = grade_run(spec=spec, obs=obs, meta=meta, tickets=tickets, users=users)
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
