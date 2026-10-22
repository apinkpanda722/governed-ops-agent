"""실제 A 저장소(읽기 전용)로 추출·검증한다. A_ROOT가 없으면 건너뛴다(CI용)."""

import os
import subprocess
from pathlib import Path

import pytest

from govops.eval import meta_extract
from govops.eval.doc_titles import load_doc_titles
from govops.eval.meta import load_meta

A_ROOT = Path(
    os.environ.get("A_ROOT", "~/PycharmProjects/ko-docs-agentic-rag")
).expanduser()

pytestmark = [
    pytest.mark.skipif(
        not (A_ROOT / "data/synthetic/plan.jsonl").is_file(),
        reason="A_ROOT에 A 저장소가 없다",
    ),
    pytest.mark.skipif(
        not (A_ROOT / "data/synthetic/ledger.sqlite").is_file(),
        reason="A의 합성 원장(ledger.sqlite, gitignore) 없음",
    ),
]


def a_head() -> str:
    return subprocess.run(
        ["git", "-C", str(A_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()


def test_real_a_extracts_and_validates(tmp_path):
    report = meta_extract.extract(A_ROOT, tmp_path / "out", expected={})
    meta = load_meta(tmp_path / "out/meta.json")
    assert meta.docs and meta.facts
    # plan.jsonl이 참조하는 사실은 모두 추출되어 있어야 한다(서비스 사실 포함).
    known = meta.fact_by_id
    assert all(fid in known for doc in meta.docs for fid in doc.fact_ids)
    services = [f for f in meta.facts if f.source_kind == "service"]
    assert services and all(f.fact_id.startswith("f-service-") for f in services)
    assert all(f.owner_team for f in services)
    if a_head().startswith(meta_extract.EXPECTED_A_COMMIT):
        # 기준 커밋(d2eec9c)에서의 기대값은 코드에 고정되어 있다.
        assert meta_extract.EXPECTED_COUNTS["doc_count"] == len(meta.docs) == 200
        assert report["a_commit_matches_expected"]
        assert len(services) == 48
        assert len(meta.facts) == 458
        assert len(meta.tickets) == 60
        assert sum(d.canary_fact_id is not None for d in meta.docs) == 30
        assert sum(d.injection_id is not None for d in meta.docs) == 12
        full = meta_extract.extract(A_ROOT, tmp_path / "full")
        assert full["expectations_match"]


def test_real_a_meta_generates_the_taskset(tmp_path):
    """추출 결과가 60 task 생성·명세 검증까지 이어진다(B 시드는 저장소 것을 쓴다).

    추출 결과가 커밋된 `data/eval/meta.json`과 같으면 생성은 그 파일로 하는 다른 테스트
    (`test_other_specs_keep_their_hashes_on_the_real_corpus`)가 이미 검증하므로 건너뛴다.
    """
    import json

    from govops.eval.meta import meta_sha256
    from govops.eval.spec import validate_specs
    from govops.eval.taskgen import generate_tasks, summarize

    meta_extract.extract(A_ROOT, tmp_path / "out", expected={})
    committed = Path(__file__).resolve().parents[1] / "data/eval/meta.json"
    if committed.is_file() and meta_sha256(committed) == meta_sha256(
        tmp_path / "out/meta.json"
    ):
        return
    meta = load_meta(tmp_path / "out/meta.json")
    seed = Path(__file__).resolve().parents[1] / "data/seed"
    tickets = json.loads((seed / "tickets.json").read_text())["tickets"]
    users = json.loads((seed / "users.json").read_text())["users"]
    # 실제 생성 경로(taskgen.main)와 같이 문서 제목을 넘긴다. 없으면 doc_ok가 늘 거짓이다(#258).
    titles = load_doc_titles(tmp_path / "out/doc_titles.json")
    specs = generate_tasks(meta, tickets, users, titles=titles)
    validate_specs(specs, meta, users, tickets)
    assert all(summarize(specs)["checks"].values())
    assert len(specs) == 60

    def without_commit(path):
        data = json.loads(path.read_text())
        data.pop("a_commit", None)
        return data

    if committed.is_file() and without_commit(committed) == without_commit(
        tmp_path / "out/meta.json"
    ):
        # a_commit만 다를 때 meta digest(taskgen.py 168행)에서 파생되는 필드는 둘뿐이다:
        # provenance.meta_sha256(431행)와 이를 포함해 해시하는 spec_sha256(438행, spec.py 135행).
        # task_id는 유형·순번(411행)이라 digest와 무관하므로 비교에서 빼지 않는다.
        def body(spec):
            data = spec if isinstance(spec, dict) else spec.model_dump(mode="json")
            data = json.loads(json.dumps(data))
            data.pop("spec_sha256")
            data["provenance"].pop("meta_sha256")
            return data

        official = json.loads((committed.parent / "tasks.json").read_text())
        if [body(s) for s in specs] != [body(s) for s in official]:
            # 내용을 출력하지 않는다(data/eval 원문 비노출).
            pytest.fail(
                "regenerated specs differ from data/eval/tasks.json", pytrace=False
            )
