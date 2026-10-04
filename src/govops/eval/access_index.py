"""요청자 기준 가시성 색인: 장애 기록과 값이 읽을 수 있는 문서에 있는지 판정한다.

- 장애 **기록**: 그 장애의 보고 문서(`doc_type == "incident"`이고 장애 사실을 담은 문서)와
  연결 티켓(`incident_id`). 다른 공개 문서가 장애 사실 일부를 언급하는 것은 기록이 아니다.
- 값 가시성: 요청자가 읽을 수 있는 문서가 그 값을 담은 사실을 가지고 있는가.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from govops.eval.meta import DocMeta, EvalMeta
from govops.eval.visibility import can_read

_INCIDENT = re.compile(r"INC-\d{4}-\d+")


def _readable(user: Mapping[str, Any], row: Mapping[str, Any] | DocMeta) -> bool:
    get = (lambda k: getattr(row, k)) if isinstance(row, DocMeta) else row.__getitem__
    return can_read(
        user, get("access_level"), get("access_teams"), get("access_min_grade")
    )


class AccessIndex:
    def __init__(self, meta: EvalMeta, tickets: Sequence[Mapping[str, Any]]) -> None:
        facts = meta.fact_by_id
        self._docs = meta.doc_by_id
        self._record_docs: dict[str, list[DocMeta]] = {}
        self._value_docs: dict[str, set[str]] = {}
        for doc in meta.docs:
            for fid in doc.fact_ids:
                fact = facts[fid]
                self._value_docs.setdefault(fact.value, set()).add(doc.doc_id)
                if fact.source_kind == "incident" and doc.doc_type == "incident":
                    bucket = self._record_docs.setdefault(fact.source_id, [])
                    if doc not in bucket:
                        bucket.append(doc)
        self._record_tickets: dict[str, list[Mapping[str, Any]]] = {}
        for ticket in tickets:
            if ticket.get("incident_id"):
                self._record_tickets.setdefault(ticket["incident_id"], []).append(
                    ticket
                )

    def record_docs(self, incident: str) -> list[DocMeta]:
        return list(self._record_docs.get(incident, []))

    def record_tickets(self, incident: str) -> list[Mapping[str, Any]]:
        return list(self._record_tickets.get(incident, []))

    def record_visible(self, user: Mapping[str, Any], incident: str) -> bool:
        """요청자가 그 장애의 기록(보고 문서·연결 티켓)을 하나라도 읽을 수 있는가."""
        return any(_readable(user, d) for d in self.record_docs(incident)) or any(
            _readable(user, t) for t in self.record_tickets(incident)
        )

    def value_visible(self, user: Mapping[str, Any], value: str) -> bool:
        """요청자가 읽을 수 있는 문서가 그 값을 담은 사실을 가지고 있는가."""
        return any(
            _readable(user, self._docs[i]) for i in self._value_docs.get(value, ())
        )

    @staticmethod
    def incident_of(request: str) -> str | None:
        found = _INCIDENT.search(request)
        return found.group(0) if found else None
