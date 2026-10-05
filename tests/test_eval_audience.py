"""발송 범위 규칙, 섞인 문서 제외, edit_approve 수정본, 채점 시점 금지 목록."""

import json
from pathlib import Path

import pytest
from test_eval_taskgen import fixture, generated

from govops.eval.audience import (
    audience_covered,
    channel_readers,
    mixed_incident_docs,
    request_channel,
    scope_violated,
    value_covered,
)
from govops.eval.meta import EvalMeta
from govops.eval.spec import EDIT_MARKER, TaskSpec, spec_hash
from govops.eval.visibility import can_read

ROOT = Path(__file__).resolve().parents[1]


def user(team, grade):
    return {"label": f"demo-{team}-g{grade}", "team": team, "grade": grade}


USERS = [user(t, g) for t in ("hr", "sre") for g in (1, 4)]
PUBLIC = {"access_level": "public", "access_teams": [], "access_min_grade": 1}
TEAM = {"access_level": "team", "access_teams": ["hr"], "access_min_grade": 1}
RESTRICTED = {
    "access_level": "restricted",
    "access_teams": ["hr"],
    "access_min_grade": 4,
}


def test_channel_readers():
    assert request_channel("INC-1 요약해서 team-hr에 올려줘") == "team-hr"
    assert request_channel("전사 공지(announce)에 공유") == "announce"
    assert request_channel("INC-2025-001 요약") is None
    assert [u["label"] for u in channel_readers("team-hr", USERS)] == [
        "demo-hr-g1",
        "demo-hr-g4",
    ]
    assert len(channel_readers("announce", USERS)) == 4


def test_audience_must_fit_inside_the_sources_readers():
    hr = channel_readers("team-hr", USERS)
    everyone = channel_readers("announce", USERS)
    assert audience_covered(hr, PUBLIC) and audience_covered(everyone, PUBLIC)
    assert audience_covered(hr, TEAM)  # 팀 전용 문서를 그 팀 채널로
    assert not audience_covered(everyone, TEAM)  # 팀 전용 문서를 전사 공지로
    assert not audience_covered(hr, RESTRICTED)  # 직급 제한 문서는 팀 채널에도 안 됨
    assert not audience_covered(channel_readers("team-sre", USERS), TEAM)


def test_generated_specs_never_send_wider_than_the_source_is_readable():
    meta, tickets, users = fixture()
    specs, _ = generated(True)
    sent = [s for s in specs if s.type in {"T2", "T3", "T6"}]
    assert len(sent) == 30
    for spec in sent:
        assert not scope_violated(spec, meta, users, tickets), spec.task_id
    # 규칙이 켜져 있어야 실제로 걸러졌는지 알 수 있다: 범위 제한을 넘는 문서를 직접 넣어 본다.
    spec = next(s for s in specs if s.subtype == "all_hands")
    team_doc = next(d.doc_id for d in meta.docs if d.access_level == "team")
    wider = spec.model_copy(
        update={
            "provenance": spec.provenance.model_copy(
                update={"source_doc_ids": [team_doc]}
            )
        }
    )
    assert scope_violated(wider, meta, users, tickets)


def test_value_covered_needs_a_readable_document_for_every_reader():
    meta, _, users = fixture()
    hr = [u for u in users if u["team"] == "hr"]
    value = next(f.value for f in meta.facts if f.value.startswith("INCIDENT_CAUSE"))
    assert value_covered(hr, meta, value) or not value_covered(users, meta, value)
    assert not value_covered(
        users, meta, value
    )  # 팀 전용 문서에만 있는 값을 전 직원에게


def test_mixed_incident_documents_exclude_meetings():
    meta, _, _ = fixture()
    base = meta.model_dump()
    facts = {f["fact_id"]: f for f in base["facts"]}
    for doc in base["docs"][:2]:
        # 첫 문서는 지침, 둘째는 회의록으로 두고 서로 다른 장애 사실을 섞는다
        doc["doc_type"] = "guide" if doc is base["docs"][0] else "meeting"
        other = next(
            f
            for f in facts.values()
            if f["source_kind"] == "incident"
            and f["source_id"] != facts[doc["fact_ids"][0]]["source_id"]
        )
        doc["fact_ids"].append(other["fact_id"])
    mixed = mixed_incident_docs(EvalMeta.model_validate(base))
    assert mixed == {base["docs"][0]["doc_id"]}


def test_edit_approve_specs_carry_a_deterministic_edit_append():
    specs, _ = generated(True)
    edits = [s for s in specs if s.subtype == "edit_approve"]
    assert len(edits) == 2
    for spec in edits:
        assert spec.approval_script[0].edit_append.startswith(EDIT_MARKER)
    for spec in specs:
        if spec.subtype != "edit_approve":
            assert all(a.edit_append is None for a in spec.approval_script)


def test_specs_without_an_edit_append_hash_and_dump_exactly_as_before():
    specs, _ = generated(True)
    plain = next(s for s in specs if s.subtype == "reject")
    dumped = plain.model_dump()
    assert all("edit_append" not in step for step in dumped["approval_script"])
    assert spec_hash(plain) == plain.spec_sha256
    TaskSpec.model_validate(dumped)


def test_edit_append_is_required_exactly_for_edit_approve():
    from govops.eval.spec import SpecValidationError, validate_specs

    meta, tickets, users = fixture()
    specs, _ = generated(True)
    spec = next(s for s in specs if s.subtype == "edit_approve")
    step = spec.approval_script[0].model_copy(update={"edit_append": "수정 표식 없음"})
    broken = spec.model_copy(update={"approval_script": [step]})
    broken = broken.model_copy(update={"spec_sha256": spec_hash(broken)})
    with pytest.raises(SpecValidationError, match="edit_append"):
        validate_specs([broken], meta, users, tickets)


def test_audit_reports_scope_and_mixed_documents():
    from govops.eval.spec_audit import audit_specs

    meta, tickets, users = fixture()
    specs, _ = generated(True)
    spec = next(s for s in specs if s.subtype == "all_hands")
    team_doc = next(d.doc_id for d in meta.docs if d.access_level == "team")
    wider = spec.model_copy(
        update={
            "provenance": spec.provenance.model_copy(
                update={"source_doc_ids": [team_doc]}
            )
        }
    )
    rules = {v.rule for v in audit_specs([wider], meta, users, tickets)}
    assert "audience_scope" in rules
    assert not {v.rule for v in audit_specs([spec], meta, users, tickets)} & {
        "audience_scope",
        "doc_mixed_incidents",
    }


def test_real_corpus_changes_only_the_expected_specs():
    meta_path = ROOT / "data/eval/meta.json"
    pinned = json.loads((ROOT / "tests/fixtures/pinned_spec_hashes.json").read_text())
    if not meta_path.is_file() or not (ROOT / "data/eval/tasks.json").is_file():
        pytest.skip("data/eval 없음")
    current = {
        s["task_id"]: s["spec_sha256"]
        for s in json.loads((ROOT / "data/eval/tasks.json").read_text())
    }
    assert set(current) == set(pinned["unchanged"]) | set(pinned["changed"])
    for task_id, sha in pinned["unchanged"].items():
        assert current[task_id] == sha, task_id
    for task_id, sha in pinned["changed"].items():
        assert current[task_id] != sha, task_id


def test_injection_documents_are_only_targets_of_indirect_injection_specs():
    from govops.eval.spec import SpecValidationError, validate_specs
    from govops.eval.spec_audit import audit_specs

    meta, tickets, users = fixture()
    specs, _ = generated(True)
    for spec in specs:
        if spec.subtype != "indirect_injection":
            assert not any(
                meta.doc_by_id[d].injection_id for d in spec.provenance.source_doc_ids
            ), spec.task_id
    spec = next(s for s in specs if s.subtype == "doc_summary")
    asker = next(u for u in users if u["label"] == spec.user)
    injected = next(
        d.doc_id
        for d in meta.docs
        if d.injection_id
        and can_read(asker, d.access_level, d.access_teams, d.access_min_grade)
    )
    bad = spec.model_copy(
        update={
            "provenance": spec.provenance.model_copy(
                update={"source_doc_ids": [injected]}
            )
        }
    )
    bad = bad.model_copy(update={"spec_sha256": spec_hash(bad)})
    assert "injection_doc" in {v.rule for v in audit_specs([bad], meta, users, tickets)}
    with pytest.raises(SpecValidationError, match="injection_doc"):
        validate_specs([bad], meta, users, tickets)
