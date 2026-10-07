"""Deterministic outbound audience checks using an access-only seed index."""

import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from govops.agent.checks import CheckResult, check_recipient_and_pii
from govops.agent.draft import Draft, target_of
from govops.agent.users import UserInfo, load_users
from govops.guardrails.policy import TeamPolicy

SCOPE_WIDER_THAN_EVIDENCE = "scope_wider_than_evidence"
EVIDENCE_SCOPE_UNKNOWN = "evidence_scope_unknown"
TICKET_NOT_FOUND = "ticket_not_found"
SCOPE_REASONS = frozenset(
    {SCOPE_WIDER_THAN_EVIDENCE, EVIDENCE_SCOPE_UNKNOWN, TICKET_NOT_FOUND}
)
_MESSAGES = {
    SCOPE_WIDER_THAN_EVIDENCE: (
        "보낼 곳의 독자 범위가 근거 문서의 열람 범위보다 넓어 발송할 수 없습니다. "
        "근거의 열람 범위에 맞는 곳으로 요청해 주세요."
    ),
    EVIDENCE_SCOPE_UNKNOWN: "근거의 열람 범위를 확인할 수 없어 발송할 수 없습니다",
    TICKET_NOT_FOUND: "티켓을 찾을 수 없습니다",
}


@dataclass(frozen=True)
class AccessRow:
    level: str
    teams: tuple[str, ...]
    min_grade: int

    def __post_init__(self) -> None:
        if (
            self.level not in {"public", "team", "restricted"}
            or not isinstance(self.teams, (list, tuple))
            or any(not isinstance(team, str) or not team for team in self.teams)
            or type(self.min_grade) is not int
            or self.min_grade < 0
        ):
            raise ValueError("Invalid access row")
        object.__setattr__(self, "teams", tuple(self.teams))


def can_read(user: UserInfo, row: AccessRow) -> bool:
    if row.level == "public":
        return True
    if user.team not in row.teams:
        return False
    if row.level == "team":
        return True
    return (
        row.level == "restricted"
        and type(row.min_grade) is int
        and user.grade >= row.min_grade
    )


@dataclass(frozen=True)
class ScopeIndex:
    docs: Mapping[str, AccessRow]
    tickets: Mapping[str, AccessRow]
    users: tuple[UserInfo, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "docs", MappingProxyType(dict(self.docs)))
        object.__setattr__(self, "tickets", MappingProxyType(dict(self.tickets)))
        object.__setattr__(self, "users", tuple(self.users))

    @classmethod
    def load(
        cls,
        index_path: str | Path | None = None,
        users_path: str | Path | None = None,
    ) -> "ScopeIndex":
        raw = json.loads(Path(index_path or "data/seed/access_index.json").read_bytes())

        def rows(kind: str) -> dict[str, AccessRow]:
            source = raw[kind]
            if not isinstance(source, dict):
                raise TypeError("Invalid access index")
            result = {}
            for identifier, fields in source.items():
                if (
                    not isinstance(identifier, str)
                    or not identifier
                    or not isinstance(fields, dict)
                    or set(fields) != {"level", "teams", "min_grade"}
                ):
                    raise ValueError("Invalid access index record")
                result[identifier] = AccessRow(**fields)
            return result

        return cls(
            rows("docs"),
            rows("tickets"),
            tuple(load_users(users_path or "data/seed/users.json").values()),
        )

    def readers(self, draft: Draft) -> list[UserInfo]:
        target = target_of(draft)
        if draft.tool == "slack_send_message":
            if target == "announce":
                return list(self.users)
            return [user for user in self.users if target == f"team-{user.team}"]
        row = self.ticket_row(target)
        return [user for user in self.users if row is not None and can_read(user, row)]

    def doc_row(self, doc_id: str) -> AccessRow | None:
        return self.docs.get(doc_id)

    def ticket_row(self, ticket_id: str) -> AccessRow | None:
        return self.tickets.get(ticket_id)


def evidence_ids(tool: str, payload: Mapping[str, Any]) -> set[str]:
    """Extract only records returned by known read-tool result structures."""
    if tool == "run_sql":
        columns = payload.get("columns", ())
        rows = payload.get("rows", ())
        if not isinstance(columns, (list, tuple)) or "ticket_id" not in columns:
            return set()
        if not isinstance(rows, (list, tuple)):
            return set()
        column = columns.index("ticket_id")
        return {
            row[column]
            for row in rows
            if isinstance(row, (list, tuple))
            and len(row) > column
            and isinstance(row[column], str)
            and re.fullmatch(r"[A-Z]{3}-\d+", row[column])
        }
    if tool == "get_document":
        records = (payload,)
    elif tool in {"lookup_id", "search_docs", "jira_search_tickets"}:
        key = {
            "lookup_id": "documents",
            "search_docs": "results",
            "jira_search_tickets": "tickets",
        }[tool]
        records = payload.get(key, ())
        if not isinstance(records, (list, tuple)):
            return set()
    elif tool == "jira_get_ticket":
        records = (payload,)
    else:
        return set()
    documents = tool in {"get_document", "lookup_id", "search_docs"}
    result = set()
    for record in records:
        if not isinstance(record, Mapping):
            continue
        identifier = record.get("doc_id" if documents else "ticket_id")
        if not isinstance(identifier, str) or not identifier:
            continue
        if documents and (
            not isinstance(record.get("text"), str) or not record["text"]
        ):
            continue
        result.add(identifier)
    return result


def check_scope(
    draft: Draft,
    evidence: Collection[str],
    *,
    requester: UserInfo,
    scope: ScopeIndex,
) -> CheckResult:
    def blocked(reason: str) -> CheckResult:
        return CheckResult("blocked", reason, draft, _MESSAGES[reason])

    if draft.tool != "slack_send_message":
        ticket = scope.ticket_row(target_of(draft))
        if ticket is None or not can_read(requester, ticket):
            return blocked(TICKET_NOT_FOUND)
    rows = []
    for identifier in sorted(evidence):
        row = scope.doc_row(identifier) or scope.ticket_row(identifier)
        if row is None:
            return blocked(EVIDENCE_SCOPE_UNKNOWN)
        rows.append(row)
    readers = scope.readers(draft)
    if any(not can_read(user, row) for row in rows for user in readers):
        return blocked(SCOPE_WIDER_THAN_EVIDENCE)
    return CheckResult("passed", None, draft, None)


def run_checks(
    draft: Draft,
    *,
    evidence: Collection[str],
    requester: UserInfo,
    policy: TeamPolicy,
    scope: ScopeIndex,
) -> CheckResult:
    initial = check_recipient_and_pii(draft, requester=requester, policy=policy)
    if initial.status == "blocked":
        return initial
    scoped = check_scope(initial.draft, evidence, requester=requester, scope=scope)
    return scoped if scoped.status == "blocked" else initial
