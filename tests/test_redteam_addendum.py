"""M6 레드팀 보조 사전 등록 문서와 사람 검수 확정본의 일관성."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADDENDUM = ROOT / "docs/results/hypotheses-m6-redteam-addendum.md"
REGISTRATION = "docs/results/hypotheses-m4-redteam.md"
CONFIRMED = ROOT / "data/redteam/review/confirmed.json"
CATEGORIES = {
    "boundary_overlap": 2,
    "jamo_decomposition": 1,
    "destination_unspecified": 4,
    "neutralized_structural": 5,
    "neutralized_no_source": 4,
    "fabrication_induced": 4,
}


def doc():
    return ADDENDUM.read_text(encoding="utf-8")


def machine_lines():
    found = {}
    for key in CATEGORIES:
        match = re.search(
            rf"^- {key}: (RT-[A-Z]-\d+(?:, RT-[A-Z]-\d+)*)$", doc(), re.MULTILINE
        )
        assert match, key
        found[key] = match.group(1).split(", ")
    return found


def set_rows():
    items = json.loads((ROOT / "data/redteam/set.json").read_text(encoding="utf-8"))
    return {row["id"]: row for row in items}


def test_addendum_binds_the_reviewed_set_and_the_confirmed_file():
    manifest = json.loads((ROOT / "data/redteam/manifest.json").read_text())
    text = doc()
    assert f"set_sha256: {manifest['set_sha256']}" in text
    raw = CONFIRMED.read_bytes()
    assert f"confirmed_sha256: {hashlib.sha256(raw).hexdigest()}" in text
    confirmed = json.loads(raw)
    assert confirmed["set_sha256"] == manifest["set_sha256"]
    assert f"reviewer: {confirmed['reviewer']}" in text
    verdicts = [row["verdict"] for row in confirmed["items"].values()]
    assert verdicts.count("approved") == 120 and verdicts.count("excluded") == 0


def test_classified_ids_equal_the_memo_ids_in_the_confirmed_file():
    confirmed = json.loads(CONFIRMED.read_text(encoding="utf-8"))
    memo_ids = {key for key, row in confirmed["items"].items() if row.get("memo")}
    lines = machine_lines()
    assert {key: len(ids) for key, ids in lines.items()} == CATEGORIES
    classified = [i for ids in lines.values() for i in ids]
    assert len(classified) == len(set(classified)) == 20
    assert set(classified) == memo_ids


def test_every_classified_id_exists_and_belongs_to_the_expected_variant():
    rows = set_rows()
    lines = machine_lines()
    for ids in lines.values():
        assert all(i in rows for i in ids)
    variants = {key: {rows[i]["variant"] for i in ids} for key, ids in lines.items()}
    assert variants["boundary_overlap"] == {"role_change"}
    assert variants["jamo_decomposition"] == {"obfuscated_ko"}
    assert variants["neutralized_structural"] == {"claim_other_user_message"}
    assert variants["neutralized_no_source"] == {"ask_rrn_lookup"}
    assert variants["fabrication_induced"] <= {"ask_rrn_lookup", "ask_pii_in_summary"}
    assert {rows[i]["category"] for i in lines["destination_unspecified"]} == {
        "indirect_injection"
    }
    # 구조적 무력화 변형은 항목 전체(5건)다.
    whole = {
        i for i, row in rows.items() if row["variant"] == "claim_other_user_message"
    }
    assert whole == set(lines["neutralized_structural"])
    # 판정 규칙이 가리키는 층은 등록과 같다.
    assert all(
        rows[i]["expected_layer"] == "send_check"
        for i in lines["neutralized_no_source"] + lines["fabrication_induced"]
    )
    assert all(
        rows[i]["expected_layer"] == "tool_quarantine"
        for i in lines["destination_unspecified"]
    )


def test_addendum_states_the_new_result_category_and_its_limits():
    text = doc()
    assert "neutralized_by_design" in text
    assert "layer_match=false" in text and "따로" in text
    assert "stopped_layer=none" in text and "breach=false" in text
    assert "목적지 불특정" in text
    for i in ("RT-X-22", "RT-X-27", "RT-X-28", "RT-X-29"):
        assert i in text


def test_addendum_has_no_result_figures():
    # 절 번호(3.1 등)는 제목 줄이므로 뺀다.
    text = "\n".join(l for l in doc().splitlines() if not l.startswith("#"))
    # 결과 수치(비율·분수·퍼센트·점수)와 채택/미달 문구가 없다. 검수 건수 같은 정수는 결정 내용이다.
    assert not re.search(r"\d\s*%|\d+\s*/\s*\d+|\d+\.\d+", text)
    assert not re.search(
        r"차단율\s*[:=]?\s*\d|포착률\s*[:=]?\s*\d|TCR\s*[:=]?\s*\d", text
    )
    assert not re.search(r"채택|미달", text)


def test_registration_documents_are_unchanged_since_their_registration():
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.split()

    assert git("rev-parse", "--is-shallow-repository") == ["false"]
    # 레드팀 등록 문서는 추가된 뒤 한 번도 수정되지 않았다(커밋 한 개).
    assert len(git("log", "--format=%H", "--", REGISTRATION)) == 1
    # Guardrail 등록 문서는 진행분 보존 커밋을 거쳐 완성됐으므로 커밋 수가 아니라,
    # 등록 때 dev manifest가 기록한 해시와 같은지로 본다.
    manifest = json.loads(
        (
            ROOT / "data/results/dev/e36589778c2243388f09d9b4d3b60f2b/manifest.json"
        ).read_text()
    )
    raw = (ROOT / "docs/results/hypotheses-m4-guardrail.md").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == manifest["hypotheses_m4_guardrail_sha256"]
