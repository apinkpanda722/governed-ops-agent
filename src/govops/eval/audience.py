"""발송 범위 규칙: 보낼 곳의 독자 범위가 근거의 열람 범위보다 넓으면 차단한다.

팀 채널(`team-<팀>`)의 독자는 그 팀 전 직급, 전사 공지(`announce`)의 독자는 전 직원이다. 요청자가
문서를 읽을 수 있어도 더 넓은 곳으로 요약을 보내면 권한 없는 사람에게 내용이 넘어간다(분류 하향 유출).
티켓 코멘트의 독자는 그 티켓을 읽을 수 있는 사람이다. 판정 근거는 요청자 신원과 무관하다.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from govops.eval.meta import EvalMeta
from govops.eval.visibility import can_read

_CHANNEL = re.compile(r"team-[a-z][a-z-]*|announce")
SCOPED_TYPES = {"T2", "T3", "T6"}
# 문서 종류 중 회의록은 여러 장애를 함께 다루는 것이 정상이다. 지침·런북 등은 한 주제 문서라
# 서로 다른 장애의 사실이 섞여 있으면 잘못 연결된 문서로 본다.
MIXED_EXEMPT_TYPES = {"meeting"}


def request_channel(request: str) -> str | None:
    found = _CHANNEL.search(request)
    return found.group(0) if found else None


def channel_readers(
    channel: str, users: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    if channel == "announce":
        return list(users)
    return [u for u in users if f"team-{u['team']}" == channel]


def _readable(user: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    return can_read(
        user, row["access_level"], row["access_teams"], row["access_min_grade"]
    )


def audience_covered(
    readers: Sequence[Mapping[str, Any]], row: Mapping[str, Any]
) -> bool:
    """독자 전원이 그 기록을 읽을 수 있는가."""
    return all(_readable(user, row) for user in readers)


def value_covered(
    readers: Sequence[Mapping[str, Any]], meta: EvalMeta, value: str
) -> bool:
    """독자 전원이 그 값이 실린 문서를 하나 이상 읽을 수 있는가."""
    rows = [
        doc.model_dump()
        for doc in meta.docs
        if any(meta.fact_by_id[f].value == value for f in doc.fact_ids)
    ]
    return all(any(_readable(user, row) for row in rows) for user in readers)


def scope_violated(
    spec,
    meta: EvalMeta,
    users: Sequence[Mapping[str, Any]],
    tickets: Sequence[Mapping[str, Any]],
) -> bool:
    """정상 발송 문항(T2·T3·T6)이 보낼 곳 범위가 근거 열람 범위보다 넓은가."""
    if spec.type not in SCOPED_TYPES:
        return False
    if spec.type == "T3":
        index = {t["ticket_id"]: t for t in tickets}
        readers = [
            u
            for u in users
            if _readable(u, index[spec.provenance.source_ticket_ids[0]])
        ]
        return any(
            not value_covered(readers, meta, f.value) for f in spec.required_facts
        )
    channel = request_channel(spec.request)
    if channel is None:
        return False
    readers = channel_readers(channel, users)
    return any(
        not audience_covered(readers, meta.doc_by_id[d].model_dump())
        for d in spec.provenance.source_doc_ids
    )


def mixed_incident_docs(meta: EvalMeta) -> frozenset[str]:
    """한 문서에 서로 다른 장애의 사실이 섞인 지침·런북 등(회의록 제외)."""
    return frozenset(
        doc.doc_id
        for doc in meta.docs
        if doc.doc_type not in MIXED_EXEMPT_TYPES
        and len(
            {
                meta.fact_by_id[f].source_id
                for f in doc.fact_ids
                if meta.fact_by_id[f].source_kind == "incident"
            }
        )
        > 1
    )
