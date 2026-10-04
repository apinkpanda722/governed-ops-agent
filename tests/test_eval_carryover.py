"""요청 문장만 바뀐 문항의 이전 판정 이어받기(카드 미리 채움)."""

import json
import subprocess
from pathlib import Path

import pytest
from test_eval_taskgen import generated

from govops.eval.carryover import carry_over, load_committed_specs
from govops.eval.review import load_confirmed_rows
from govops.eval.spec import spec_content_sha256, spec_hash


def row(spec, status="approved", note=""):
    return {"status": status, "spec_sha256": spec_hash(spec), "note": note}


def reword(spec, request):
    changed = spec.model_copy(update={"request": request})
    return changed.model_copy(update={"spec_sha256": spec_hash(changed)})


def test_content_hash_ignores_only_the_request():
    spec = generated(True)[0][0]
    other = reword(spec, "전혀 다른 문장")
    assert spec_hash(spec) != spec_hash(other)
    assert spec_content_sha256(spec) == spec_content_sha256(other)
    moved = spec.model_copy(update={"split": "test" if spec.split == "dev" else "dev"})
    assert spec_content_sha256(spec) != spec_content_sha256(moved)


def test_only_request_changed_specs_inherit_the_previous_decision():
    new = list(generated(True)[0])
    old = [reword(s, s.request + " (이전 문장)") for s in new]
    decisions = {s.task_id: row(s, "needs_change", "메모") for s in old}
    found = carry_over(new, old, decisions)
    assert set(found) == {s.task_id for s in new}
    first = found[new[0].task_id]
    assert (first.status, first.note) == ("needs_change", "메모")
    assert first.previous_request == old[0].request


def test_anything_else_changed_is_not_inherited():
    new = list(generated(True)[0])
    old = [reword(s, s.request + " (이전)") for s in new]
    # 요청 문장 외에 바뀐 문항: 결과 기대치가 다르다
    changed = new[0].model_copy(
        update={"expected": new[0].expected.model_copy(update={"outcome": "blocked"})}
    )
    changed = changed.model_copy(update={"spec_sha256": spec_hash(changed)})
    new2 = [changed, *new[1:]]
    decisions = {s.task_id: row(s) for s in old}
    found = carry_over(new2, old, decisions)
    assert new[0].task_id not in found and len(found) == len(new) - 1


def test_stale_decisions_and_unchanged_specs_are_not_carried():
    new = list(generated(True)[0])
    old = [reword(s, s.request + " (이전)") for s in new]
    decisions = {s.task_id: row(s) for s in old}
    # 결정이 더 오래된 해시에 대한 것이면 이어받지 않는다
    decisions[new[1].task_id] = {**decisions[new[1].task_id], "spec_sha256": "0" * 64}
    # 문장이 같은(해시가 같은) 문항은 이어받기가 아니라 기존 확정이다
    old[2] = new[2]
    decisions[new[2].task_id] = row(new[2])
    # 이전 판정이 없는 문항
    del decisions[new[3].task_id]
    found = carry_over(new, old, decisions)
    assert not {new[1].task_id, new[2].task_id, new[3].task_id} & set(found)
    assert len(found) == len(new) - 3


def test_missing_previous_spec_is_not_carried():
    new = list(generated(True)[0])
    old = [reword(s, "이전") for s in new[1:]]  # 첫 문항은 이전 명세가 없다(교체·신규)
    found = carry_over(new, old, {s.task_id: row(s) for s in old})
    assert new[0].task_id not in found


def test_load_committed_specs_reads_the_last_commit_only(tmp_path):
    specs = list(generated(True)[0])
    from govops.eval.spec import dump_specs

    repo = tmp_path / "repo"
    (repo / "data/eval").mkdir(parents=True)
    dump_specs(specs, repo / "data/eval/tasks.json")
    git = [
        "git",
        "-C",
        str(repo),
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
    ]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], check=True)
    # 작업 트리의 새 명세로 덮어써도 커밋된 이전 명세를 읽는다
    dump_specs([reword(s, "새 문장") for s in specs], repo / "data/eval/tasks.json")
    committed = load_committed_specs(repo / "data/eval/tasks.json")
    assert [s.request for s in committed] == [s.request for s in specs]
    # git 저장소가 아니거나 커밋이 없으면 None(이어받기 없음)
    assert load_committed_specs(tmp_path / "none/tasks.json") is None


def test_load_confirmed_rows_is_read_only_and_validates(tmp_path):
    spec = generated(True)[0][0]
    path = tmp_path / "confirmed.json"
    assert load_confirmed_rows(path) == {}
    path.write_text(
        json.dumps(
            {
                "reviewer": "r",
                "reviewed_at": "2026-10-05",
                "tasks": {spec.task_id: row(spec, "rejected", "x")},
            }
        )
    )
    before = path.read_bytes()
    assert load_confirmed_rows(path)[spec.task_id]["status"] == "rejected"
    assert path.read_bytes() == before
    path.write_text("{}")
    with pytest.raises(ValueError):
        load_confirmed_rows(path)
    assert Path
