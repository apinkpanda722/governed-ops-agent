"""Approval identity policy and immutable destination edits."""

import json
from pathlib import Path

import pytest

from govops.agent.approval import (
    EDITABLE_FIELDS,
    Decision,
    InvalidEdit,
    apply_edit,
    approval_id,
    approver_decision,
)
from govops.agent.draft import Draft, draft_sha256
from govops.agent.users import UserInfo, load_users

DRAFTS = [
    Draft("slack_send_message", {"channel": "announce", "text": "초안"}),
    Draft("slack_send_message", {"channel": "team-platform", "text": "초안"}),
    Draft("jira_add_comment", {"ticket_id": "PLT-1001", "body": "초안"}),
    Draft(
        "jira_create_followup_ticket",
        {
            "parent_ticket_id": "PLT-1001",
            "title": "제목",
            "description": "초안",
            "priority": "high",
        },
    ),
]


@pytest.mark.parametrize("draft", DRAFTS)
@pytest.mark.parametrize("grade", [2, 4, 6])
@pytest.mark.parametrize("actor", ["self", "g3", "g4", "g5", "other", "unknown"])
def test_approver_policy_matrix(draft, grade, actor):
    requester = UserInfo("requester", "platform", grade)
    approver = {
        "self": UserInfo("requester", "platform", grade),
        "g3": UserInfo("same-g3", "platform", 3),
        "g4": UserInfo("same-g4", "platform", 4),
        "g5": UserInfo("same-g5", "platform", 5),
        "other": UserInfo("other-g4", "payments", 4),
        "unknown": None,
    }[actor]
    announce = (
        draft.tool == "slack_send_message" and draft.args["channel"] == "announce"
    )
    expected = Decision.NOT_FOUND
    if actor == "self":
        expected = Decision.FORBIDDEN if announce else Decision.ALLOW
    elif announce and actor in {"g4", "g5"}:
        expected = Decision.ALLOW
    assert approver_decision(draft, requester=requester, approver=approver) == expected


@pytest.mark.parametrize(
    "subtype",
    ["other_user_approve", "all_hands_approver", "all_hands", "aggregate_all_hands"],
)
def test_evaluation_approval_scripts(subtype):
    tasks = json.loads(Path("data/eval/tasks.json").read_text())
    users = load_users()
    selected = [t for t in tasks if t["subtype"] == subtype]
    assert selected
    for task in selected:
        requester = users[task["user"]]
        target = task["expected"]["effects"][0]["target"]
        draft = Draft("slack_send_message", {"channel": target, "text": "합성 요약"})
        actors = {
            "requester": requester,
            "same_team_g4": UserInfo("approval-peer-g4", requester.team, 4),
            "other_team_user": next(
                u for u in users.values() if u.team != requester.team
            ),
        }
        for action in task["approval_script"]:
            expected = {
                None: Decision.ALLOW,
                403: Decision.FORBIDDEN,
                404: Decision.NOT_FOUND,
            }[action["expect_http"]]
            assert (
                approver_decision(
                    draft, requester=requester, approver=actors[action["actor"]]
                )
                == expected
            )


@pytest.mark.parametrize("draft", DRAFTS)
def test_editable_fields_and_preservation(draft):
    expected = {
        "slack_send_message": frozenset({"text"}),
        "jira_add_comment": frozenset({"body"}),
        "jira_create_followup_ticket": frozenset({"title", "description"}),
    }
    assert dict(EDITABLE_FIELDS) == expected
    original = dict(draft.args)
    for field in expected[draft.tool]:
        result = apply_edit(draft, {field: "수정 내용"})
        assert result is not draft
        assert result.tool == draft.tool
        assert dict(result.args) == {**original, field: "수정 내용"}
        assert draft_sha256(result) != draft_sha256(draft)
        assert dict(draft.args) == original


@pytest.mark.parametrize("draft", DRAFTS)
@pytest.mark.parametrize(
    "field",
    [
        "channel",
        "ticket_id",
        "parent_ticket_id",
        "priority",
        "unknown",
        "tool",
        "user",
        "team",
        "grade",
    ],
)
def test_forbidden_edit_keys(draft, field):
    with pytest.raises(InvalidEdit):
        apply_edit(draft, {field: draft.args.get(field, "값")})


@pytest.mark.parametrize("draft", DRAFTS)
@pytest.mark.parametrize("value", ["", " \n\t", None, 4, True, [], {}, "가" * 4001])
def test_invalid_edit_values(draft, value):
    for field in EDITABLE_FIELDS[draft.tool]:
        with pytest.raises(InvalidEdit):
            apply_edit(draft, {field: value})


def test_edit_boundaries_and_no_policy_checks():
    draft = DRAFTS[0]
    assert len(apply_edit(draft, {"text": "가" * 4000}).args["text"]) == 4000
    assert apply_edit(draft, {}).args == draft.args
    assert apply_edit(draft, {"text": "010-1234-5678"}).args["text"] == "010-1234-5678"
    with pytest.raises(InvalidEdit):
        apply_edit(draft, {"text": "valid", "channel": "announce"})


def test_approval_id_binds_task_and_hash_only():
    sha = draft_sha256(DRAFTS[0])
    result = approval_id("task-1", sha)
    assert result == approval_id("task-1", sha)
    assert result != approval_id("task-2", sha)
    assert result != approval_id(
        "task-1", draft_sha256(apply_edit(DRAFTS[0], {"text": "수정"}))
    )
    assert len(result) == 64
    assert set(result) <= set("0123456789abcdef")
    assert "task-1" not in result and "초안" not in result
    assert approval_id("ab", "c") != approval_id("a", "bc")
