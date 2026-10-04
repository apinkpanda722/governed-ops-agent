"""명세 감사: 금지 값 규칙(전 유형)과 no_permission 선정 규칙을 점검한다.

- 규칙 2(전 유형): 금지 값은 요청자가 읽을 수 있는 어떤 문서에도 없는 값만 쓴다.
- no_permission: 요청자가 그 장애의 기록(보고 문서·연결 티켓)을 읽을 수 없고, 금지 값이 하나
  이상 있으며, 기대 결과가 `not_found_or_partial`이다.
위반은 (task_id, 규칙 이름)만 돌려준다(값·본문 없음).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from govops.eval.access_index import AccessIndex
from govops.eval.meta import EvalMeta
from govops.eval.spec import TaskSpec


@dataclass(frozen=True)
class Violation:
    task_id: str
    rule: str


def audit_specs(
    specs: Sequence[TaskSpec],
    meta: EvalMeta,
    users: Sequence[Mapping[str, Any]],
    tickets: Sequence[Mapping[str, Any]],
    *,
    index: AccessIndex | None = None,
) -> list[Violation]:
    index = index or AccessIndex(meta, tickets)
    by_label = {u["label"]: u for u in users}
    found = []
    for spec in specs:
        user = by_label[spec.user]
        if any(index.value_visible(user, v) for v in spec.forbidden.leak_values):
            found.append(Violation(spec.task_id, "leak_value_readable"))
        if spec.subtype == "no_permission":
            incident = index.incident_of(spec.request)
            if spec.expected.outcome != "not_found_or_partial":
                found.append(Violation(spec.task_id, "outcome"))
            if incident is None or index.record_visible(user, incident):
                found.append(Violation(spec.task_id, "incident_record_readable"))
            if not spec.forbidden.leak_values:
                found.append(Violation(spec.task_id, "leak_values_missing"))
    return found
