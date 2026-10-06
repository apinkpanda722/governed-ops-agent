"""채점 시점 금지 목록 자동 계산: 명세·해시는 그대로, 읽을 수 없는 기록만 합친다."""

import json
from pathlib import Path

import pytest
from test_eval_taskgen import fixture, generated

from govops.eval.forbidden_auto import auto_forbidden, with_auto_forbidden
from govops.eval.meta import load_meta
from govops.eval.spec import spec_hash
from govops.eval.visibility import can_read

ROOT = Path(__file__).resolve().parents[1]


def readable(user, row):
    row = row if isinstance(row, dict) else row.model_dump()
    return can_read(
        user, row["access_level"], row["access_teams"], row["access_min_grade"]
    )


def test_unreadable_records_are_added_and_readable_ones_are_not():
    meta, tickets, users = fixture()
    specs, _ = generated(True)
    by_user = {u["label"]: u for u in users}
    for spec in specs:
        user = by_user[spec.user]
        forbidden = auto_forbidden(
            spec, meta, tickets, users, grading_rules="registered"
        )
        assert set(spec.forbidden.leak_doc_ids) <= set(forbidden.leak_doc_ids)
        assert set(spec.forbidden.leak_values) <= set(forbidden.leak_values)
        for doc_id in forbidden.leak_doc_ids:
            assert doc_id in spec.forbidden.leak_doc_ids or not readable(
                user, meta.doc_by_id[doc_id]
            )
        for ticket_id in forbidden.leak_ticket_ids:
            ticket = next(t for t in tickets if t["ticket_id"] == ticket_id)
            assert ticket_id in spec.forbidden.leak_ticket_ids or not readable(
                user, ticket
            )
        # 읽을 수 있는 문서에도 있는 값은 금지 목록에 없다(규칙 2)
        readable_values = {
            meta.fact_by_id[f].value
            for d in meta.docs
            if readable(user, d)
            for f in d.fact_ids
        }
        added = set(forbidden.leak_values) - set(spec.forbidden.leak_values)
        assert not added & readable_values
        # 요청 문장에 이미 나온 ID는 금지 목록에 넣지 않는다
        assert not any(i in spec.request for i in added)
    # 읽을 수 없는 canary 값은 전부 들어 있다(5개 고정 목록이 아니다)
    inj = next(s for s in specs if s.subtype == "indirect_injection")
    unreadable_canaries = {
        c.value
        for c in meta.canaries
        if not readable(by_user[inj.user], meta.doc_by_id[c.doc_id])
    }
    assert unreadable_canaries <= set(
        auto_forbidden(
            inj, meta, tickets, users, grading_rules="registered"
        ).leak_values
    )


def test_the_spec_and_its_hash_are_untouched():
    meta, tickets, users = fixture()
    spec = generated(True)[0][0]
    graded = with_auto_forbidden(spec, meta, tickets, users, grading_rules="registered")
    assert spec_hash(spec) == spec.spec_sha256
    assert graded.spec_sha256 == spec.spec_sha256
    assert graded.request == spec.request


def test_real_corpus_adds_the_missing_ids_for_the_t5_specs():
    meta_path = ROOT / "data/eval/meta.json"
    if not meta_path.is_file():
        pytest.skip("data/eval/meta.json 없음")
    meta = load_meta(meta_path)
    tickets = json.loads((ROOT / "data/seed/tickets.json").read_text())["tickets"]
    users = json.loads((ROOT / "data/seed/users.json").read_text())["users"]
    from govops.eval.spec import TaskSpec

    specs = [
        TaskSpec.model_validate(s)
        for s in json.loads((ROOT / "data/eval/tasks.json").read_text())
    ]
    by_id = {s.task_id: s for s in specs}
    by_user = {u["label"]: u for u in users}
    for task_id in ("T5-01", "T5-02", "T5-03", "T5-04"):
        spec = by_id[task_id]
        forbidden = auto_forbidden(
            spec, meta, tickets, users, grading_rules="registered"
        )
        hidden_tickets = {
            t["ticket_id"] for t in tickets if not readable(by_user[spec.user], t)
        }
        assert set(forbidden.leak_ticket_ids) == hidden_tickets - {
            t for t in hidden_tickets if t in spec.request
        } | set(spec.forbidden.leak_ticket_ids)
        assert set(spec.forbidden.leak_doc_ids) <= set(forbidden.leak_doc_ids)
    all_canaries = {c.value for c in meta.canaries}
    for task_id in ("T5-08", "T5-09", "T5-10"):
        spec = by_id[task_id]
        unreadable = {
            c.value
            for c in meta.canaries
            if not readable(by_user[spec.user], meta.doc_by_id[c.doc_id])
        }
        assert unreadable <= set(
            auto_forbidden(
                spec, meta, tickets, users, grading_rules="registered"
            ).leak_values
        )
        assert unreadable <= all_canaries


@pytest.mark.parametrize("kind", ["doc", "ticket"])
def test_visible_fact_id_exception_and_own_preservation(kind):
    from govops.eval.meta import FactMeta
    from govops.eval.rules import unreadable_ids_for

    meta, tickets, users = fixture()
    spec = generated(True)[0][0]
    user = next(u for u in users if u["label"] == spec.user)
    baseline = auto_forbidden(spec, meta, tickets, users, grading_rules="registered")
    field = "leak_doc_ids" if kind == "doc" else "leak_ticket_ids"
    hidden = next(
        i for i in getattr(baseline, field) if i not in getattr(spec.forbidden, field)
    )
    readable_doc = next(d for d in meta.docs if readable(user, d))
    fact = FactMeta(
        fact_id="f-visible-id",
        kind="id",
        value=hidden,
        source_kind="event",
        source_id="synthetic",
        owner_team=None,
    )
    changed = meta.model_copy(
        update={
            "facts": [*meta.facts, fact],
            "docs": [
                d.model_copy(update={"fact_ids": [*d.fact_ids, fact.fact_id]})
                if d.doc_id == readable_doc.doc_id
                else d
                for d in meta.docs
            ],
        }
    )
    old = auto_forbidden(spec, changed, tickets, users, grading_rules="registered")
    new = auto_forbidden(spec, changed, tickets, users, grading_rules="m3-grading")
    assert hidden in getattr(old, field)
    assert hidden not in getattr(new, field)
    assert old.leak_values == new.leak_values
    assert set(getattr(new, field)) == set(getattr(old, field)) - {hidden}
    assert hidden not in unreadable_ids_for(
        user, changed, tickets, grading_rules="m3-grading"
    )
    own = spec.model_copy(
        update={"forbidden": spec.forbidden.model_copy(update={field: [hidden]})}
    )
    assert hidden in getattr(
        auto_forbidden(own, changed, tickets, users, grading_rules="m3-grading"), field
    )
