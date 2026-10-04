"""Offline review cards containing only declarative specs and metadata."""

import json
from collections.abc import Mapping, Sequence
from html import escape
from pathlib import Path

from govops.eval.meta import EvalMeta
from govops.eval.review import REVIEW_PATH, ReviewFormatError, review_report
from govops.eval.spec import TaskSpec, spec_hash
from govops.eval.visibility import can_read

COMMON_CHECKS = (
    "요청 문장이 자연스럽고 한 가지 일만 요청하는가",
    "요청자가 소스를 읽을 수 있는가(또는 읽을 수 없어야 하는가) 표와 일치하는가",
    "필수 fact가 근거 문서에 실제로 있는가",
    "금지 값이 요청 문장에 노출되지 않았는가",
)
CHANNEL_CHECKS = (
    "수신 채널이 허용 목록(자기 팀 채널·전사 공지)인가",
    "전사 공지는 승인자 정책이 맞는가",
)
TYPE_CHECKS = {
    "T1": ("요약해야 할 사실이 충분한가",),
    "T2": CHANNEL_CHECKS,
    "T3": ("대상 티켓을 요청자가 읽을 수 있는가", "후속 티켓의 원 티켓 연결이 있는가"),
    "T4": CHANNEL_CHECKS + ("집계 값이 요청자 기준 가시 티켓으로 계산되었는가",),
    "T5": (
        "올바른 거절 기준(없음과 구별되지 않는 응답)이 명세와 맞는가",
        "간접 인젝션 문서가 요청자에게 읽히는가",
    ),
    "T6": ("승인 시나리오 단계의 행위자·기대 응답이 맞는가",),
}
STYLE = "<style>body{font-family:sans-serif;line-height:1.5;margin:2em}table{border-collapse:collapse;width:100%}td,th{border:1px solid #aaa;padding:.4em;overflow-wrap:anywhere}article{break-inside:avoid;border-top:2px solid;margin-top:2em}pre{white-space:pre-wrap;overflow-wrap:anywhere}@media print{article{break-before:page}nav{display:none}}</style>"


def _e(value):
    return escape(str(value), quote=True)


def _json(value):
    return "<pre>" + _e(json.dumps(value, ensure_ascii=False, indent=2)) + "</pre>"


def _table(headers, rows):
    return (
        "<table><tr>"
        + "".join("<th>" + _e(h) + "</th>" for h in headers)
        + "</tr>"
        + "".join(
            "<tr>" + "".join("<td>" + _e(v) + "</td>" for v in row) + "</tr>"
            for row in rows
        )
        + "</table>"
    )


def _page(title, body):
    return (
        '<!doctype html><html lang="ko"><meta charset="utf-8"><title>'
        + _e(title)
        + "</title>"
        + STYLE
        + "<body><h1>"
        + _e(title)
        + "</h1>"
        + body
        + "</body></html>"
    )


def build_cards(
    specs: Sequence[TaskSpec],
    meta: EvalMeta,
    users: Sequence[Mapping],
    tickets: Sequence[Mapping],
    out_dir: Path = Path("data/interim/eval/cards/"),
) -> list[Path]:
    # Prevent output publication into the human review directory, even via symlinks.
    if (
        out_dir.resolve() == REVIEW_PATH.parent.resolve()
        or REVIEW_PATH.parent.resolve() in out_dir.resolve().parents
    ):
        raise ValueError("cards output cannot be the review directory")
    identities = {u["label"]: u for u in users}
    states = {s.task_id: "미검수" for s in specs}
    try:
        report = review_report(specs, REVIEW_PATH)
        for key, label in [
            ("confirmed", "승인"),
            ("rejected", "반려"),
            ("needs_change", "수정 필요"),
            ("stale", "무효"),
        ]:
            states.update(dict.fromkeys(getattr(report, key), label))
    except ReviewFormatError:
        pass
    cards = {f"T{i}": [] for i in range(1, 7)}
    rows = []
    for s in specs:
        user = identities[s.user]

        def readable(row, identity=user):
            return (
                "요청자가 읽을 수 있음"
                if can_read(
                    identity,
                    row["access_level"],
                    row["access_teams"],
                    row["access_min_grade"],
                )
                else "요청자가 읽을 수 없음"
            )

        body = '<article id="' + _e(s.task_id) + '"><h2>' + _e(s.task_id) + "</h2>"
        body += _table(
            ["유형", "하위유형", "split", "executable_in", "요청자", "팀", "직급"],
            [
                [
                    s.type,
                    s.subtype,
                    s.split,
                    s.executable_in,
                    s.user,
                    user["team"],
                    user["grade"],
                ]
            ],
        )
        body += (
            "<h3>요청 문장</h3><p>"
            + _e(s.request)
            + "</p><h3>기대 상태·승인 시나리오 단계</h3>"
            + _json(s.expected.model_dump())
            + _json([a.model_dump() for a in s.approval_script])
        )
        body += "<h3>필수 fact</h3>" + _table(
            ["fact_id", "kind", "value", "mode"],
            [[f.fact_id, f.kind, f.value, f.mode] for f in s.required_facts],
        )
        docs = [
            d
            for d in meta.docs
            if d.doc_id in s.provenance.source_doc_ids
            or any(f.fact_id in d.fact_ids for f in s.required_facts)
        ]
        body += "<h3>근거 발췌(메타데이터만)</h3>" + _table(
            [
                "doc_id",
                "doc_type",
                "team",
                "접근 수준",
                "허용 팀",
                "최소 직급",
                "가시성",
                "fact_ids",
            ],
            [
                [
                    d.doc_id,
                    d.doc_type,
                    d.team,
                    d.access_level,
                    ", ".join(d.access_teams),
                    d.access_min_grade,
                    readable(d.model_dump()),
                    ", ".join(d.fact_ids),
                ]
                for d in docs
            ],
        )
        body += _table(
            ["fact_id", "source_kind", "source_id"],
            [
                [f.fact_id, f.source_kind, f.source_id]
                for f in meta.facts
                if any(f.fact_id == r.fact_id for r in s.required_facts)
            ],
        )
        body += _table(
            ["소스 티켓", "가시성"],
            [
                [t["ticket_id"], readable(t)]
                for t in tickets
                if t["ticket_id"] in s.provenance.source_ticket_ids
            ],
        )
        body += "<h3>derived_values·계산 근거</h3>" + _json(
            [v.model_dump() for v in s.derived_values]
        )
        if s.type == "T4":
            conditions = s.request.split("`")[1::2][:2]
            visible = [
                t["ticket_id"]
                for t in tickets
                if len(conditions) == 2
                and t["owner_team"] == conditions[0]
                and t["status"] == conditions[1]
                and readable(t) == "요청자가 읽을 수 있음"
            ]
            body += _json(
                {
                    "owner_team·status 조건": conditions,
                    "요청자 가시 티켓": visible,
                    "건수": len(visible),
                }
            )
        body += (
            "<h3>금지 사항</h3>"
            + _json(s.forbidden.model_dump())
            + "<h3>필수 도구 후보</h3>"
            + _json(s.required_tools_any)
        )
        body += (
            "<h3>확인 항목</h3><ul>"
            + "".join(
                "<li>□ " + _e(c) + "</li>" for c in COMMON_CHECKS + TYPE_CHECKS[s.type]
            )
            + "</ul></article>"
        )
        cards[s.type].append(body)
        rows.append(
            '<tr data-type="'
            + _e(s.type)
            + '" data-split="'
            + _e(s.split)
            + '" data-executable="'
            + _e(s.executable_in)
            + '"><td><a href="'
            + _e(s.type)
            + ".html#"
            + _e(s.task_id)
            + '">'
            + _e(s.task_id)
            + "</a></td>"
            + "".join(
                "<td>" + _e(v) + "</td>"
                for v in [
                    s.type,
                    s.split,
                    s.executable_in,
                    spec_hash(s)[:12],
                    states[s.task_id],
                ]
            )
            + "</tr>"
        )
    filters = "<nav>"
    for name, values in [
        ("type", cards.keys()),
        ("split", ["dev", "test"]),
        ("executable", ["m2", "m3"]),
    ]:
        filters += (
            "<label>"
            + _e(name)
            + '<select data-filter="'
            + _e(name)
            + '"><option value="">전체</option>'
            + "".join("<option>" + _e(v) + "</option>" for v in values)
            + "</select></label> "
        )
    filters += '</nav><script>document.querySelectorAll("select").forEach(s=>s.onchange=()=>document.querySelectorAll("tr[data-type]").forEach(r=>r.hidden=Array.from(document.querySelectorAll("select")).some(f=>f.value&&r.dataset[f.dataset.filter]!==f.value)));</script>'
    index = (
        filters
        + "<table><tr><th>task_id</th><th>유형</th><th>split</th><th>executable_in</th><th>명세 해시</th><th>확정 상태</th></tr>"
        + "".join(rows)
        + "</table>"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = [("index.html", _page("문항 검수 목록", index))] + [
        (typ + ".html", _page(typ, "".join(content))) for typ, content in cards.items()
    ]
    paths = []
    for name, content in outputs:
        path = out_dir / name
        if path.is_symlink():
            raise ValueError("cards output cannot be a symlink")
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    return paths
