import ast
import hashlib
import json
import re
import shutil
import subprocess
from html import escape, unescape
from pathlib import Path

import pytest
import yaml
from test_eval_taskgen import fixture

from govops.eval.a_source import ASource, ASourceError
from govops.eval.cards import build_cards
from govops.eval.cards_assets import SCRIPT
from govops.eval.cards_text import access_reason, excerpt, fact_label
from govops.eval.review import review_report
from govops.eval.review_cli import main
from govops.eval.spec import dump_specs, load_specs, spec_hash

TEAM_NAMES = {
    "data-ai": "데이터AI",
    "finance": "재무",
    "hr": "인사",
    "legal": "법무컴플라이언스",
    "payments": "결제서비스",
    "platform": "플랫폼개발",
    "security": "정보보안",
    "sre": "SRE인프라",
}


def make_a_tree(root: Path, meta) -> Path:
    """합성 A 저장소: 팀·사람 시드와 meta의 모든 문서 JSON(본문·fact_spans)."""
    seed = root / "data/seed"
    (seed / "people").mkdir(parents=True)
    (seed / "teams.yaml").write_text(
        yaml.safe_dump(
            {"teams": [{"code": c, "name_ko": n} for c, n in TEAM_NAMES.items()]},
            allow_unicode=True,
        )
    )
    (seed / "people/platform.yaml").write_text(
        yaml.safe_dump(
            {
                "people": [
                    {
                        "person_id": "p-001",
                        "name": "합성이름",
                        "team": "platform",
                        "grade": 5,
                        "role": "개발",
                    }
                ]
            },
            allow_unicode=True,
        )
    )
    docs = root / "data/synthetic/docs"
    docs.mkdir(parents=True)
    facts = meta.fact_by_id
    injections = {i.doc_id: i for i in meta.injections}
    for doc in meta.docs:
        body = f"# 제목 {doc.doc_id}\n\n서두 문장이다. "
        spans = []
        canary = injection = None
        for fid in doc.fact_ids:
            body += "앞 문장이다. 값은 "
            start = len(body)
            body += facts[fid].value
            spans.append({"fact_id": fid, "start": start, "end": len(body)})
            if fid == doc.canary_fact_id:
                canary = {"fact_id": fid, "start": start, "end": len(body)}
            body += " 이다. 뒤 문장이다. "
        if doc.doc_id in injections:
            start = len(body)
            body += "숨은 지시 문구 & <b>다</b>"
            injection = {
                "start": start,
                "end": len(body),
                "variant": "synthetic",
                "injection_id": injections[doc.doc_id].injection_id,
            }
        (docs / f"{doc.doc_id}.json").write_text(
            json.dumps(
                {
                    "doc_id": doc.doc_id,
                    "title": f"제목 {doc.doc_id}",
                    "body": body,
                    "fact_spans": spans,
                    "canary": canary,
                    "injection": injection,
                },
                ensure_ascii=False,
            )
        )
    (docs / "real-doc.json").write_text(
        json.dumps({"doc_id": "real-doc", "body": "REAL_SENTINEL"})
    )
    return root


@pytest.fixture
def world(tmp_path):
    from test_eval_taskgen import generated

    meta, tickets, users = fixture()
    specs = list(
        generated(True)[0]
    )  # 같은 fixture의 캐시된 생성 결과(목록은 복사해 쓴다)
    a_root = make_a_tree(tmp_path / "a", meta)
    source = ASource(a_root, {d.doc_id for d in meta.docs})
    return meta, tickets, users, specs, source, tmp_path


def card_text(html: str, task_id: str) -> str:
    return re.search(rf'<article id="{task_id}".*?</article>', html, re.DOTALL).group(0)


# ---------- 순수 함수 ----------


def test_excerpt_hit_matches_the_span_and_keeps_neighbouring_sentences():
    body = "첫째 문장이다. 둘째 문장이다. 값은 SECRET 이다. 넷째 문장이다. 다섯째 문장이다."
    start = body.index("SECRET")
    before, hit, after = excerpt(body, start, start + 6)
    assert hit == "SECRET" == body[start : start + 6]
    assert "둘째 문장이다." in before and "첫째" not in before
    assert "넷째 문장이다." in after and "다섯째" not in after


def test_excerpt_truncates_very_long_neighbours():
    body = "가" * 1000 + " 값 " + "나" * 1000
    start = body.index("값")
    before, hit, after = excerpt(body, start, start + 1)
    assert hit == "값" and len(before) < 300 and len(after) < 300


@pytest.mark.parametrize(
    "level,teams,grade,user,expected,words",
    [
        ("public", [], 1, {"team": "hr", "grade": 1}, True, "공개"),
        ("team", ["hr"], 1, {"team": "hr", "grade": 1}, True, "인사팀 전용"),
        ("team", ["hr"], 1, {"team": "sre", "grade": 4}, False, "소속 팀이 아니라"),
        ("restricted", ["hr"], 4, {"team": "hr", "grade": 3}, False, "직급이 모자라"),
        ("restricted", ["hr"], 4, {"team": "hr", "grade": 4}, True, "4급 이상 전용"),
    ],
)
def test_access_reason_is_plain_language(level, teams, grade, user, expected, words):
    ok, text = access_reason(user, TEAM_NAMES.get, level, teams, grade)
    assert ok is expected and words in text


def test_fact_labels_are_human_words():
    assert fact_label("f-inc-2025-001-cause") == "원인"
    assert fact_label("f-service-core-gateway-slo") == "가용성 목표"
    assert fact_label("f-canary-001") == "기밀 식별 문자열"
    assert fact_label("f-x-unknown") == "unknown"


# ---------- A 읽기 ----------


def test_a_source_reads_only_listed_syn_docs(world, monkeypatch):
    _, _, _, _, _, tmp = world
    opened = []
    original = Path.read_text

    def spy(self, *args, **kwargs):
        opened.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)
    source = ASource(tmp / "a", {"syn-doc-000"})
    assert source.doc("syn-doc-000").title == "제목 syn-doc-000"
    assert source.doc("syn-doc-001") is None  # 메타데이터에 없으면 열지 않는다
    assert source.doc("real-doc") is None and source.doc("../seed/teams") is None
    names = {p.name for p in opened}
    assert "real-doc.json" not in names and "syn-doc-001.json" not in names
    assert (
        source.team_name("hr") == "인사" and source.person("p-001").name == "합성이름"
    )


def test_a_source_requires_the_seed(tmp_path):
    with pytest.raises(ASourceError):
        ASource(tmp_path, set())


# ---------- 카드 ----------


def test_cards_are_plain_korean_with_evidence_and_no_external_resources(world):
    meta, tickets, users, specs, source, tmp = world
    out = tmp / "cards"
    paths = build_cards(specs, meta, users, tickets, source, out)
    assert [p.name for p in paths] == ["index.html"]
    html = paths[0].read_text()
    assert html.count("<article ") == 60
    assert all(v not in html for v in ["http://", "https://", "src=", "REAL_SENTINEL"])
    for marker in (
        "① 질문",
        "② 정답",
        "③ 답에 들어가야 할 내용",
        "④ 권한",
        "⑤ 답에 나오면 안 되는 것",
        "⑥ 자동 점검",
        "⑦ 판정",
        "자세히",
    ):
        assert marker in html
    t1 = unescape(card_text(html, "T1-01"))
    assert "직원이 묻습니다" in t1 and "급 직원" in t1
    visible = re.sub(
        r"<details>.*?</details>", "", card_text(html, "T1-01"), flags=re.DOTALL
    )
    assert (
        "fact_id" not in visible
        and "rubric_only" not in visible
        and '"outcome"' not in visible
    )


def test_excerpts_highlight_exactly_the_fact_span(world):
    meta, tickets, users, specs, source, tmp = world
    html = build_cards(specs, meta, users, tickets, source, tmp / "cards")[
        0
    ].read_text()
    spec = next(s for s in specs if s.task_id == "T1-01")
    card = card_text(html, spec.task_id)
    for fact in spec.required_facts:
        assert f'<mark class="">{escape(fact.value)}</mark>' in card
        for doc_id in spec.provenance.source_doc_ids:
            text = source.doc(doc_id)
            span = next(x for x in text.fact_spans if x.fact_id == fact.fact_id)
            assert text.body[span.start : span.end] == fact.value


def test_user_and_a_text_is_escaped(world):
    meta, tickets, users, specs, source, tmp = world
    specs[0] = specs[0].model_copy(update={"request": "<script>alert(1)</script>&"})
    html = build_cards(specs, meta, users, tickets, source, tmp / "cards")[
        0
    ].read_text()
    assert "<script>alert(1)" not in html
    assert escape("<script>alert(1)</script>&") in html
    inj = next(s for s in specs if s.subtype == "indirect_injection")
    assert "<b>다</b>" not in card_text(html, inj.task_id)
    assert escape("숨은 지시 문구 & <b>다</b>") in card_text(html, inj.task_id)


def test_forbidden_values_show_where_they_hide_and_why(world):
    meta, tickets, users, specs, source, tmp = world
    html = build_cards(specs, meta, users, tickets, source, tmp / "cards")[
        0
    ].read_text()
    spec = next(s for s in specs if s.subtype == "indirect_injection")
    card = card_text(html, spec.task_id)
    assert "숨은 지시" in card and 'mark class="bad"' in card
    assert "읽을 수 없" in unescape(card)


def test_auto_check_failures_warn_at_the_top(world):
    meta, tickets, users, specs, source, tmp = world
    spec = next(s for s in specs if s.forbidden.leak_values)
    leaked = spec.model_copy(
        update={"request": spec.request + " " + spec.forbidden.leak_values[0]}
    )
    specs = [leaked if s.task_id == spec.task_id else s for s in specs]
    html = build_cards(specs, meta, users, tickets, source, tmp / "cards")[
        0
    ].read_text()
    card = card_text(html, spec.task_id)
    assert 'class="warn"' in card and 'class="banner"' in card
    assert "✗" in card and "금지 값·ID가 질문에 들어 있지 않다" in unescape(card)
    clean = card_text(html, next(s.task_id for s in specs if s.task_id != spec.task_id))
    assert "✓" in clean


def test_no_permission_cards_use_the_record_level_answer_and_have_no_warning(world):
    from govops.eval.cards_content import NO_PERMISSION_ANSWER

    meta, tickets, users, specs, source, tmp = world
    html = build_cards(specs, meta, users, tickets, source, tmp / "cards")[
        0
    ].read_text()
    cards = [s for s in specs if s.subtype == "no_permission"]
    assert len(cards) == 4
    for spec in cards:
        card = card_text(html, spec.task_id)
        text = unescape(card)
        assert NO_PERMISSION_ANSWER in text
        assert 'data-warn="0"' in card and "✗" not in card
        assert "장애 기록" in text and "읽을 수 없음" in text
        assert "장애의 기록(장애 보고 문서·연결 티켓)을 읽을 수 없다" in text


def test_a_legacy_no_permission_spec_is_flagged(world):
    meta, tickets, users, specs, source, tmp = world
    legacy = next(s for s in specs if s.subtype == "no_permission")
    legacy = legacy.model_copy(
        update={"expected": legacy.expected.model_copy(update={"outcome": "not_found"})}
    )
    specs = [legacy if s.task_id == legacy.task_id else s for s in specs]
    html = build_cards(specs, meta, users, tickets, source, tmp / "cards")[
        0
    ].read_text()
    card = card_text(html, legacy.task_id)
    assert 'data-warn="1"' in card and "정의되어 있다" in unescape(card)


def test_slack_target_check_does_not_apply_to_ticket_tasks(world):
    meta, tickets, users, specs, source, tmp = world
    html = build_cards(specs, meta, users, tickets, source, tmp / "cards")[
        0
    ].read_text()
    for spec in specs:
        if spec.type == "T3":
            assert "수신처가" not in card_text(html, spec.task_id)


def test_t4_shows_calculated_answer_and_basis(world):
    meta, tickets, users, specs, source, tmp = world
    html = unescape(
        build_cards(specs, meta, users, tickets, source, tmp / "cards")[0].read_text()
    )
    spec = next(s for s in specs if s.type == "T4")
    card = card_text(html, spec.task_id)
    value = next(v.value for v in spec.derived_values if v.name == "visible_count")
    assert f"정답 값: {value}건" in card and "계산 근거" in card


def test_each_card_has_three_judgments_and_a_note_and_a_save_flow(world):
    meta, tickets, users, specs, source, tmp = world
    html = build_cards(specs, meta, users, tickets, source, tmp / "cards")[
        0
    ].read_text()
    assert (
        html.count('value="approved"')
        == html.count('value="needs_change"')
        == html.count('value="rejected"')
        == 60
    )
    assert html.count('class="note"') == 60
    for label in ("맞음", "고칠 것 있음", "문항 제외"):
        assert label in html
    for element in (
        'id="reviewer"',
        'id="reviewed_at"',
        'id="save"',
        "localStorage",
        "confirmed.json",
    ):
        assert element in html
    for sha in {spec_hash(s) for s in specs}:
        assert sha in html


def test_cards_output_never_targets_the_review_directory(world, monkeypatch):
    meta, tickets, users, specs, source, _ = world
    with pytest.raises(ValueError):
        build_cards(specs, meta, users, tickets, source, Path("data/eval/review"))


def test_old_per_type_pages_are_removed(world):
    meta, tickets, users, specs, source, tmp = world
    out = tmp / "cards"
    out.mkdir()
    (out / "T1.html").write_text("old")
    build_cards(specs, meta, users, tickets, source, out)
    assert not (out / "T1.html").exists()


# ---------- 판정 저장(내려받는 JSON) ----------


@pytest.mark.skipif(shutil.which("node") is None, reason="node 필요")
def test_downloaded_json_passes_review_report_and_skips_unjudged(world):
    _, _, _, specs, _, tmp = world
    shas = {s.task_id: spec_hash(s) for s in specs}
    judgments = {
        specs[0].task_id: {"status": "approved", "note": ""},
        specs[1].task_id: {"status": "needs_change", "note": "요청 문장 수정"},
        specs[2].task_id: {"status": "rejected", "note": "제외"},
        specs[3].task_id: {"status": "", "note": "메모만 있음"},  # 판정 안 함
        "T9-99": {"status": "approved", "note": "없는 문항"},
    }
    script = (
        "const vm=require('vm');const ctx={};vm.createContext(ctx);"
        f"vm.runInContext({json.dumps(SCRIPT)},ctx);"
        f"const c=ctx.__cards;const out={{header:c.validateHeader('','2026-10-05'),"
        f"bad:c.validateHeader('x','10/05'),ok:c.validateHeader(' 검수자 ','2026-10-05'),"
        f"data:c.buildConfirmed(' 검수자 ','2026-10-05',{json.dumps(judgments)},{json.dumps(shas)})}};"
        "console.log(JSON.stringify(out));"
    )
    out = json.loads(
        subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True
        ).stdout
    )
    assert out["header"] and out["bad"] and out["ok"] == ""
    data = out["data"]
    assert (
        list(data) == ["reviewer", "reviewed_at", "tasks"]
        and data["reviewer"] == "검수자"
    )
    assert set(data["tasks"]) == {specs[0].task_id, specs[1].task_id, specs[2].task_id}
    assert all(
        list(v) == ["status", "spec_sha256", "note"] for v in data["tasks"].values()
    )
    path = tmp / "confirmed.json"
    path.write_text(json.dumps(data, ensure_ascii=False))
    report = review_report(specs, path)
    assert report.confirmed == [specs[0].task_id]
    assert report.needs_change == [specs[1].task_id] and report.rejected == [
        specs[2].task_id
    ]
    assert len(report.pending) == 57 and not report.stale


# ---------- CLI ----------


def test_cards_cli_needs_a_root_and_never_writes_the_confirmation(
    world, monkeypatch, capsys
):
    meta, tickets, users, specs, _, tmp = world
    monkeypatch.chdir(tmp)
    assert main(["cards"]) == 1
    assert "make eval-gen" in capsys.readouterr().out
    for name, payload in [
        ("eval/meta", meta.model_dump()),
        ("seed/users", {"users": users}),
        ("seed/tickets", {"tickets": tickets}),
    ]:
        path = tmp / f"data/{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    dump_specs(specs, tmp / "data/eval/tasks.json")
    review = tmp / "data/eval/review/confirmed.json"
    review.parent.mkdir()
    review.write_text(
        json.dumps(
            {
                "reviewer": "검수자",
                "reviewed_at": "2026-10-04",
                "tasks": {
                    s.task_id: {
                        "status": "approved",
                        "spec_sha256": spec_hash(s),
                        "note": "",
                    }
                    for s in specs
                },
            }
        )
    )
    before = (review.stat().st_mtime_ns, hashlib.sha256(review.read_bytes()).digest())
    assert main(["status"]) == 0
    assert main(["cards"]) == 1  # A_ROOT 없음
    assert "A_ROOT" in capsys.readouterr().out
    assert main(["cards", "--a-root", str(tmp / "a")]) == 0
    assert main(["cards", "--a-root", str(tmp / "nowhere")]) == 1
    assert before == (
        review.stat().st_mtime_ns,
        hashlib.sha256(review.read_bytes()).digest(),
    )
    html = (tmp / "data/interim/eval/cards/index.html").read_text()
    assert html.count('data-confirmed="approved"') == 60  # 기존 확정본을 불러온다
    payload = json.loads(review.read_text())
    payload["tasks"][specs[0].task_id]["status"] = "rejected"
    review.write_text(json.dumps(payload))
    assert main(["status"]) == 1
    review.unlink()
    assert main(["status"]) == 1


# ---------- 실제 A 데이터(없으면 건너뜀) ----------

A_ROOT = Path("~/PycharmProjects/ko-docs-agentic-rag").expanduser()


@pytest.mark.skipif(
    not (A_ROOT / "data/synthetic/docs").is_dir()
    or not Path("data/eval/tasks.json").is_file(),
    reason="A 저장소 또는 data/eval/tasks.json이 없다",
)
def test_real_data_cards_find_evidence_for_every_required_fact(tmp_path):
    from govops.eval.meta import load_meta

    meta = load_meta(Path("data/eval/meta.json"))
    users = json.loads(Path("data/seed/users.json").read_text())["users"]
    tickets = json.loads(Path("data/seed/tickets.json").read_text())["tickets"]
    specs = load_specs(Path("data/eval/tasks.json"), meta, users, tickets)
    source = ASource(A_ROOT, {d.doc_id for d in meta.docs})
    html = build_cards(specs, meta, users, tickets, source, tmp_path)[0].read_text()
    assert html.count("<article ") == 60
    assert "본문에서 근거 위치를 찾지 못했습니다" not in html
    assert "<script>alert" not in html


def test_review_writes_are_absent():
    root = Path(__file__).resolve().parents[1] / "src/govops/eval"
    for name in [
        "cards.py",
        "cards_content.py",
        "cards_text.py",
        "a_source.py",
        "review.py",
        "review_cli.py",
    ]:
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr in {
                "write_text",
                "write_bytes",
            }:
                assert name == "cards.py" and ast.unparse(node.func.value) == "path"
            text = ast.unparse(node)
            if "confirmed" in text or "REVIEW_PATH" in text:
                assert not (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"write_text", "write_bytes"}
                )
                if isinstance(node.func, ast.Name) and node.func.id == "open":
                    assert not any(
                        isinstance(a, ast.Constant)
                        and isinstance(a.value, str)
                        and any(c in a.value for c in "wax+")
                        for a in node.args[1:]
                    )
