"""정답 명세 검수 카드: 한국어 평문 카드 HTML 한 장(목록 + 카드 60장 + 판정·저장).

카드는 명세와 A 합성 문서 본문(읽기 전용)으로 만든다. 코드는 판정을 만들지 않는다. 사람이
카드에서 고른 판정만으로 `confirmed.json` 형식 파일을 내려받게 하며, 그 파일은 사람이
`data/eval/review/confirmed.json`에 두어야 확정본으로 인정된다.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from html import escape
from pathlib import Path

from govops.eval.a_source import ASource
from govops.eval.access_index import AccessIndex
from govops.eval.cards_assets import SCRIPT, STYLE
from govops.eval.cards_content import (
    TYPE_NAMES,
    CardContent,
    Context,
    Evidence,
    build_content,
)
from govops.eval.carryover import Carried, carry_over, request_only_changes
from govops.eval.meta import EvalMeta
from govops.eval.review import (
    REVIEW_PATH,
    ReviewFormatError,
    load_confirmed_rows,
    review_report,
)
from govops.eval.spec import TaskSpec, spec_hash

JUDGMENTS = (
    ("approved", "맞음"),
    ("needs_change", "고칠 것 있음"),
    ("rejected", "문항 제외"),
)
EXEC_LABELS = {"m2": "지금(M2) 실행", "m3": "M3 이후 실행"}


def _e(value) -> str:
    return escape(str(value), quote=True)


def _evidence(item: Evidence, *, bad: bool = False) -> str:
    mark = "bad" if bad else ""
    return (
        '<div class="ev"><span class="small">'
        + _e(item.doc_id)
        + " 「"
        + _e(item.title)
        + "」 · "
        + _e(item.access_text)
        + "</span><br>"
        + _e(item.before)
        + f'<mark class="{mark}">'
        + _e(item.hit)
        + "</mark>"
        + _e(item.after)
        + "</div>"
    )


def _list(items: Sequence[str]) -> str:
    return "<ul>" + "".join("<li>" + i + "</li>" for i in items) + "</ul>"


def _card(
    content: CardContent,
    state: str,
    ctx: Context,
    previous: str | None = None,
    carried: Carried | None = None,
) -> str:
    spec = content.spec
    sha = spec_hash(spec)
    warn = content.warnings
    out = (
        f'<article id="{_e(spec.task_id)}" data-task="{_e(spec.task_id)}" '
        f'data-type="{_e(spec.type)}" data-split="{_e(spec.split)}" '
        f'data-exec="{_e(spec.executable_in)}" data-sha="{_e(sha)}" '
        f'data-warn="{1 if warn else 0}" data-confirmed="{_e(state)}"'
        + (' data-carry="1"' if previous is not None else "")
        + (
            f' data-carry-status="{_e(carried.status)}" data-carry-note="{_e(carried.note)}"'
            if carried
            else ""
        )
        + (' class="warn"' if warn else "")
        + ">"
        + "<h2>"
        + _e(spec.task_id)
        + " · "
        + _e(TYPE_NAMES[spec.type])
        + "</h2>"
        + '<span class="tag">'
        + _e(spec.split)
        + '</span><span class="tag">'
        + _e(EXEC_LABELS[spec.executable_in])
        + "</span>"
    )
    if warn:
        out += (
            '<div class="banner">⚠ 자동 점검에서 확인이 필요한 항목이 있습니다: '
            + _e(" / ".join(c.text for c in warn))
            + "</div>"
        )
    if previous is not None:
        hint = (
            ' <span class="small">(이전 판정을 미리 채웠습니다. 새 문장을 확인한 뒤 '
            f'<button type="button" class="seen" data-task="{_e(spec.task_id)}">'
            "문장 확인함</button>을 누르거나 판정을 다시 고르세요)</span>"
            if carried
            else ' <span class="small">(이전 판정 없음)</span>'
        )
        out += (
            '<div class="carry">요청 문장만 바뀜 — 문장만 확인'
            + hint
            + '<br><span class="small">이전 문장: '
            + _e(previous)
            + "</span></div>"
        )
    out += (
        "<h3>① 질문</h3><p><b>"
        + _e(content.who)
        + '</b></p><div class="q">'
        + _e(spec.request)
        + "</div>"
    )
    out += "<h3>② 정답</h3>" + _list(_e(s) for s in content.answer)
    if content.approval:
        out += "<p class=small>승인 시나리오</p>" + _list(
            _e(s) for s in content.approval
        )
    if content.calc:
        out += "<p class=small>계산 근거</p>" + _list(_e(s) for s in content.calc)
    out += "<h3>③ 답에 들어가야 할 내용</h3>"
    if content.facts or content.derived:
        for fact in content.facts:
            out += (
                "<p><b>"
                + _e(fact.label)
                + "</b>: "
                + _e(fact.value)
                + ' <span class="small">('
                + _e(fact.rule)
                + ")</span></p>"
            )
            out += (
                "".join(_evidence(item) for item in fact.evidence)
                if fact.evidence
                else '<p class="ng">본문에서 근거 위치를 찾지 못했습니다.</p>'
            )
        if content.derived:
            out += _list(_e(d) for d in content.derived)
    else:
        out += "<p>없음 (내용 정확성이 아니라 올바른 처리 여부로 판정하는 문항)</p>"
    out += "<h3>④ 권한</h3>"
    if content.access:
        out += _list(
            f'<span class="{"ok" if line.readable else "ng"}">'
            + ("읽을 수 있음" if line.readable else "읽을 수 없음")
            + "</span> "
            + _e(line.kind)
            + " "
            + _e(line.item_id)
            + (" 「" + _e(line.title) + "」" if line.title else "")
            + " — "
            + _e(line.reason)
            for line in content.access
        )
    else:
        out += "<p>없음</p>"
    out += "<h3>⑤ 답에 나오면 안 되는 것</h3>"
    if content.forbidden:
        for line in content.forbidden:
            out += (
                "<p>"
                + _e(line.text)
                + ' <span class="small">— '
                + _e(line.reason)
                + "</span></p>"
            )
            if line.evidence:
                out += _evidence(line.evidence, bad=True)
    else:
        out += "<p>없음</p>"
    out += "<h3>⑥ 자동 점검</h3>" + _list(
        f'<span class="{"ok" if c.ok else "ng"}">{"✓" if c.ok else "✗"}</span> '
        + _e(c.text)
        for c in content.checks
    )
    out += (
        '<h3>⑦ 판정</h3><div class="judge">'
        + "".join(
            f'<label><input type="radio" name="j-{_e(spec.task_id)}" '
            f'data-task="{_e(spec.task_id)}" value="{value}"> {_e(label)}</label>'
            for value, label in JUDGMENTS
        )
        + f'<br>메모 <input class="note" data-task="{_e(spec.task_id)}" type="text" '
        'placeholder="고칠 점이나 제외 사유"></div>'
    )
    out += (
        "<details><summary>자세히(내부 표기)</summary>"
        "<p class=small>spec_sha256: "
        + _e(sha)
        + "</p><pre>"
        + _e(
            json.dumps(spec.model_dump(), ensure_ascii=False, indent=2, sort_keys=True)
        )
        + "</pre></details></article>"
    )
    return out


def build_cards(
    specs: Sequence[TaskSpec],
    meta: EvalMeta,
    users: Sequence[Mapping],
    tickets: Sequence[Mapping],
    source: ASource,
    out_dir: Path = Path("data/interim/eval/cards/"),
    *,
    previous_specs: Sequence[TaskSpec] | None = None,
) -> list[Path]:
    # 사람이 검수하는 디렉토리(확정본 위치)에는 쓰지 않는다. 심볼릭 링크로 우회하는 경우도 막는다.
    if (
        out_dir.resolve() == REVIEW_PATH.parent.resolve()
        or REVIEW_PATH.parent.resolve() in out_dir.resolve().parents
    ):
        raise ValueError("cards output cannot be the review directory")
    ctx = Context(
        meta=meta,
        users={u["label"]: u for u in users},
        all_users=list(users),
        tickets={t["ticket_id"]: t for t in tickets},
        source=source,
        index=AccessIndex(meta, tickets),
    )
    existing = {s.task_id: "" for s in specs}
    try:
        report = review_report(specs, REVIEW_PATH)
        for key, status in (
            ("confirmed", "approved"),
            ("rejected", "rejected"),
            ("needs_change", "needs_change"),
        ):
            existing.update(dict.fromkeys(getattr(report, key), status))
    except ReviewFormatError:
        pass
    ordered = sorted(specs, key=lambda s: s.task_id)
    contents = [build_content(s, ctx) for s in ordered]

    def storage_key(items):
        digest = hashlib.sha256("".join(spec_hash(s) for s in items).encode())
        return "govops-review-" + digest.hexdigest()[:16]

    storage = storage_key(ordered)
    # 요청 문장만 바뀐 문항(이전 명세와 비교)과 그 이전 판정. 카드는 미리 채워 보여 줄 뿐이다.
    request_only: dict[str, str] = {}
    carried: dict[str, Carried] = {}
    prev_key = ""
    if previous_specs:
        request_only = request_only_changes(ordered, previous_specs)
        prev_key = storage_key(sorted(previous_specs, key=lambda s: s.task_id))
        try:
            carried = carry_over(
                ordered, previous_specs, load_confirmed_rows(REVIEW_PATH)
            )
        except ReviewFormatError:
            carried = {}
    rows = "".join(
        f'<tr data-task="{_e(c.spec.task_id)}" data-type="{_e(c.spec.type)}" '
        f'data-split="{_e(c.spec.split)}" data-exec="{_e(c.spec.executable_in)}" '
        f'data-warn="{1 if c.warnings else 0}"'
        + (' data-carry="1"' if c.spec.task_id in request_only else "")
        + f'><td><a href="#{_e(c.spec.task_id)}">'
        f"{_e(c.spec.task_id)}</a></td><td>{_e(TYPE_NAMES[c.spec.type])}</td>"
        f"<td>{_e(c.spec.split)}</td><td>{_e(EXEC_LABELS[c.spec.executable_in])}</td>"
        f"<td>{'⚠ ' + str(len(c.warnings)) if c.warnings else '✓'}</td>"
        f'<td id="st-{_e(c.spec.task_id)}">미판정</td></tr>'
        for c in contents
    )
    options = lambda values: "".join(
        f'<option value="{_e(v)}">{_e(label)}</option>' for v, label in values
    )
    controls = (
        '<div id="controls">'
        '검수자 <input id="reviewer" type="text" placeholder="이름"> '
        '날짜 <input id="reviewed_at" type="date"> '
        '<button id="save" type="button">결과 저장(confirmed.json 내려받기)</button> '
        '<span id="progress"></span><div id="msg"></div>'
        '<label>유형 <select id="f-type"><option value="">전체</option>'
        + options((t, f"{t} {n}") for t, n in TYPE_NAMES.items())
        + '</select></label> <label>구분 <select id="f-split"><option value="">전체</option>'
        + options([("dev", "dev"), ("test", "test")])
        + '</select></label> <label>실행 <select id="f-exec"><option value="">전체</option>'
        + options(EXEC_LABELS.items())
        + '</select></label> <label><input id="f-pending" type="checkbox"> 미판정만</label> '
        '<label><input id="f-warn" type="checkbox"> 경고 있는 것만</label> '
        '<label><input id="f-carry" type="checkbox"> 문장만 확인(요청 문장만 바뀜)</label></div>'
    )
    intro = (
        "<p>각 문항의 질문과 정답을 읽고, 정답이 맞는지 판정하세요. 본문 발췌에서 노란 부분이 "
        "정답의 근거입니다. 요청 문장만 바뀐 문항에는 이전 판정이 미리 채워져 있으니 새 문장만 확인하세요. 판정은 이 브라우저에만 임시 저장되고, 위의 저장 버튼으로 "
        "<code>confirmed.json</code>을 내려받아 <code>data/eval/review/</code>에 두어야 "
        "확정본으로 인정됩니다. 판정하지 않은 문항은 파일에 들어가지 않습니다.</p>"
    )
    summary = (
        "<table><tr><th>문항</th><th>유형</th><th>구분</th><th>실행</th><th>자동 점검</th>"
        "<th>판정</th></tr>" + rows + "</table>"
    )
    page = (
        '<!doctype html><html lang="ko"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>정답 명세 검수 카드</title><style>"
        + STYLE
        + f'</style><body data-storage-key="{storage}" data-prev-key="{prev_key}">'
        "<h1>정답 명세 검수 카드</h1>"
        + intro
        + controls
        + "<h2>목록</h2>"
        + summary
        + "".join(
            _card(
                c,
                existing[c.spec.task_id],
                ctx,
                request_only.get(c.spec.task_id),
                carried.get(c.spec.task_id),
            )
            for c in contents
        )
        + "<script>"
        + SCRIPT
        + "</script></body></html>"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("T[1-6].html"):  # 이전 형식의 생성물(혼동 방지)
        if old.is_file() and not old.is_symlink():
            old.unlink()
    path = out_dir / "index.html"
    if path.is_symlink():
        raise ValueError("cards output cannot be a symlink")
    path.write_text(page, encoding="utf-8")
    return [path]
