"""V15 structural evidence and independent runtime permission checks."""

import json
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import pytest

from govops.agent.draft import Draft
from govops.agent.scope import (
    ScopeIndex,
    can_read,
    check_scope,
    evidence_ids,
    run_checks,
)
from govops.agent.users import UserInfo
from govops.audit.log import REASONS
from govops.eval.audience import audience_covered, channel_readers
from govops.eval.visibility import can_read as oracle
from govops.guardrails.policy import TeamPolicy


@pytest.fixture
def scope(tmp_path):
    rows = {
        "public": {"level": "public", "teams": [], "min_grade": 0},
        "team": {"level": "team", "teams": ["platform"], "min_grade": 0},
        "rank": {"level": "restricted", "teams": ["platform"], "min_grade": 4},
        "other": {"level": "team", "teams": ["finance"], "min_grade": 0},
    }
    index = tmp_path / "access.json"
    index.write_text(json.dumps({"docs": rows, "tickets": rows}))
    return ScopeIndex.load(index)


def message(channel="team-platform", text="요약"):
    return Draft("slack_send_message", {"channel": channel, "text": text})


REQUESTER = UserInfo("demo-platform-g1", "platform", 1)


@pytest.mark.parametrize(
    "channel,evidence,reason",
    [
        ("announce", {"team"}, "scope_wider_than_evidence"),
        ("team-platform", {"rank"}, "scope_wider_than_evidence"),
        ("team-platform", {"other"}, "scope_wider_than_evidence"),
        ("announce", {"public"}, None),
        ("team-platform", {"team"}, None),
        ("announce", set(), None),
        ("announce", {"missing"}, "evidence_scope_unknown"),
        ("announce", {"public", "team"}, "scope_wider_than_evidence"),
    ],
)
def test_scope_cases(scope, channel, evidence, reason):
    result = check_scope(message(channel), evidence, requester=REQUESTER, scope=scope)
    assert result.reason == reason
    assert result.status == ("blocked" if reason else "passed")
    if reason:
        assert reason in REASONS
        assert reason not in result.message
        if reason == "scope_wider_than_evidence":
            assert result.message.startswith(
                "보낼 곳의 독자 범위가 근거 문서의 열람 범위보다 넓어 발송할 수 없습니다"
            )
            assert "근거의 열람 범위에 맞는 곳" in result.message
        else:
            assert (
                result.message == "근거의 열람 범위를 확인할 수 없어 발송할 수 없습니다"
            )


@pytest.mark.parametrize(
    "tool,target",
    [
        ("jira_add_comment", "ticket_id"),
        ("jira_create_followup_ticket", "parent_ticket_id"),
    ],
)
def test_ticket_existence_and_inherited_readers(scope, tool, target):
    def draft(ticket):
        return Draft(
            tool, {target: ticket, "body" if target == "ticket_id" else "title": "요약"}
        )

    results = [
        check_scope(draft(t), set(), requester=REQUESTER, scope=scope)
        for t in ("other", "missing", "rank")
    ]
    assert all(
        r.status == "blocked" and r.reason == "ticket_not_found" for r in results
    )
    assert len({r.message for r in results}) == 1
    assert results[0].message == "티켓을 찾을 수 없습니다"
    assert "ticket_not_found" in REASONS
    readers = scope.readers(draft("team"))
    assert readers and all(u.team == "platform" for u in readers)
    assert {u.grade for u in readers} >= {1, 4}
    assert (
        check_scope(draft("team"), {"team"}, requester=REQUESTER, scope=scope).status
        == "passed"
    )
    assert (
        check_scope(draft("public"), {"team"}, requester=REQUESTER, scope=scope).reason
        == "scope_wider_than_evidence"
    )


@pytest.mark.parametrize(
    "tool,payload,expected",
    [
        (
            "lookup_id",
            {
                "documents": [
                    {"doc_id": "d", "text": "발췌"},
                    {"doc_id": "empty", "text": ""},
                ]
            },
            {"d"},
        ),
        ("get_document", {"doc_id": "d", "text": "본문"}, {"d"}),
        ("get_document", {"doc_id": "d", "text": ""}, set()),
        (
            "search_docs",
            {"results": [{"doc_id": "d", "text": "발췌"}, {"doc_id": "e"}]},
            {"d"},
        ),
        (
            "jira_get_ticket",
            {"ticket_id": "PLT-1001", "parent_id": "PLT-1000"},
            {"PLT-1001"},
        ),
        ("jira_get_ticket", {"error": {"code": "not_found"}}, set()),
        (
            "jira_search_tickets",
            {"tickets": [{"ticket_id": "PLT-1001"}, {"ticket_id": "FIN-1001"}]},
            {"PLT-1001", "FIN-1001"},
        ),
        (
            "run_sql",
            {"rows": [{"doc_id": "d", "text": "본문", "ticket_id": "PLT-1001"}]},
            set(),
        ),
    ],
)
def test_evidence_structure(tool, payload, expected):
    assert evidence_ids(tool, payload) == expected


def test_lookup_replay_evidence():
    records = [
        json.loads(line)
        for line in Path("data/replay/a_run/T1-01.jsonl").read_text().splitlines()
    ]
    lookup = next(r for r in records if r["tool"] == "lookup_id")
    assert evidence_ids(lookup["tool"], lookup["result"]) == {
        "syn-meeting-001",
        "syn-incident-014",
    }


def test_full_index_oracle_consistency():
    scope = ScopeIndex.load()
    raw = json.loads(Path("data/seed/access_index.json").read_text())
    users = json.loads(Path("data/seed/users.json").read_text())["users"]
    for kind, getter in (("docs", scope.doc_row), ("tickets", scope.ticket_row)):
        for identifier, fields in raw[kind].items():
            row = getter(identifier)
            for user in users:
                assert can_read(UserInfo(**user), row) == oracle(
                    user, fields["level"], fields["teams"], fields["min_grade"]
                )
    for channel in ["announce", *{f"team-{u['team']}" for u in users}]:
        readers = scope.readers(message(channel))
        assert [asdict(u) for u in readers] == channel_readers(channel, users)
        for identifier, fields in raw["docs"].items():
            row = scope.doc_row(identifier)
            assert all(can_read(u, row) for u in readers) == audience_covered(
                channel_readers(channel, users),
                {
                    "access_level": fields["level"],
                    "access_teams": fields["teams"],
                    "access_min_grade": fields["min_grade"],
                },
            )
    assert scope.doc_row("missing") is None
    assert scope.ticket_row("missing") is None


def test_composed_checks(scope):
    policy = TeamPolicy({"platform": "mask"})

    def run(draft, evidence):
        return run_checks(
            draft, evidence=evidence, requester=REQUESTER, policy=policy, scope=scope
        )

    assert run(message("team-finance"), {"missing"}).reason == "recipient_not_allowed"
    result = run(message(text="연락 010-1234-5678"), {"team"})
    assert result.status == "masked"
    assert "010-1234-5678" not in result.draft.args["text"]
    blocked = run(message("announce", "연락 010-1234-5678"), {"team"})
    assert blocked.reason == "scope_wider_than_evidence"
    assert "010-1234-5678" not in blocked.draft.args["text"]
    assert (
        run_checks(
            message(text="010-1234-5678"),
            evidence=set(),
            requester=REQUESTER,
            policy=TeamPolicy({"platform": "block"}),
            scope=scope,
        ).reason
        == "pii_blocked_by_team_policy"
    )


def test_ticket_evidence_and_custom_users(tmp_path):
    index = tmp_path / "access.json"
    users = tmp_path / "users.json"
    index.write_text(
        json.dumps(
            {
                "docs": {},
                "tickets": {
                    "PLT-1001": {"level": "team", "teams": ["platform"], "min_grade": 0}
                },
            }
        )
    )
    users.write_text(
        json.dumps(
            {
                "users": [
                    asdict(REQUESTER),
                    {"label": "demo-finance-g1", "team": "finance", "grade": 1},
                ]
            }
        )
    )
    scope = ScopeIndex.load(index, users)
    assert len(scope.readers(message("announce"))) == 2
    assert (
        check_scope(message(), {"PLT-1001"}, requester=REQUESTER, scope=scope).status
        == "passed"
    )
    assert (
        check_scope(
            message("announce"), {"PLT-1001"}, requester=REQUESTER, scope=scope
        ).reason
        == "scope_wider_than_evidence"
    )
    assert (
        check_scope(message(), {"FIN-9999"}, requester=REQUESTER, scope=scope).reason
        == "evidence_scope_unknown"
    )


def test_evidence_is_structural_and_deduplicated():
    payload = {
        "results": [
            {"doc_id": "d", "text": "본문"},
            {"doc_id": "d", "text": "발췌"},
            {"doc_id": "whitespace", "text": "   "},
            {"doc_id": "null", "text": None},
            {"doc_id": "number", "text": 1},
            {"text": "본문에만 syn-meeting-001"},
            None,
        ]
    }
    assert evidence_ids("search_docs", payload) == {"d", "whitespace"}
    assert evidence_ids("search_docs", {"results": None}) == set()
    assert evidence_ids("unknown_tool", {"doc_id": "d", "text": "본문"}) == set()


def test_scope_audit_reasons_are_accepted(tmp_path):
    from govops.audit.log import AuditLog

    audit = AuditLog(tmp_path)
    for reason in (
        "scope_wider_than_evidence",
        "evidence_scope_unknown",
        "ticket_not_found",
    ):
        audit.append(
            "side_effect.draft",
            task_id=uuid4().hex,
            actor_sub=REQUESTER.label,
            reason=reason,
        )


def test_sql_evidence_is_exact_ticket_column_only():
    from govops.agent.scope import evidence_ids

    assert evidence_ids(
        "run_sql",
        {"columns": ["ticket_id", "title"], "rows": [["SYN-1", "x"], ["bad", "SYN-2"]]},
    ) == {"SYN-1"}
    assert (
        evidence_ids("run_sql", {"columns": ["status", "count"], "rows": [["open", 3]]})
        == set()
    )


@pytest.mark.parametrize(
    "columns,rows,reason",
    [
        (["ticket_id"], [["SYN-1"], ["SYN-2"]], "scope_wider_than_evidence"),
        (["status", "count"], [["open", 2]], None),
        (["ticket_id"], [["SYN-1"]], None),
        (["ticket_id"], [["SYN-404"]], "evidence_scope_unknown"),
    ],
)
def test_sql_outbound_scope(columns, rows, reason):
    from govops.agent.scope import AccessRow

    lower = UserInfo("demo-platform-g1", "platform", 1)
    upper = UserInfo("demo-platform-g4", "platform", 4)
    index = ScopeIndex(
        {},
        {
            "SYN-1": AccessRow("team", ("platform",), 0),
            "SYN-2": AccessRow("restricted", ("platform",), 4),
        },
        (lower, upper),
    )
    result = run_checks(
        message(),
        evidence=evidence_ids("run_sql", {"columns": columns, "rows": rows}),
        requester=upper,
        scope=index,
        policy=TeamPolicy({"platform": "block"}),
    )
    assert result.reason == reason
