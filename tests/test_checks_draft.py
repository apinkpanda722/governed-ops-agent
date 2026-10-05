"""Deterministic draft, recipient and PII contracts (no services)."""

import hashlib
import unicodedata
from pathlib import Path

import pytest

from govops.agent.checks import REASONS, check_recipient_and_pii
from govops.agent.draft import Draft, body_text, draft_sha256, target_of
from govops.agent.users import UserInfo, load_users
from govops.audit.log import REASONS as AUDIT_REASONS
from govops.guardrails.pii import find_pii
from govops.guardrails.policy import load_team_policy, team_policy_sha256

POLICY_PATH = Path("data/policy/team_policy.json")
PII = [
    "900101-1234567",
    "010-2345-6789",
    "synthetic@example.invalid",
    "계좌 123-456-789012",
    "4111-1111-1111-1111",
]


def draft(text="합성 업무 안내", tool="slack_send_message", target="team-platform"):
    args = {"channel": target, "text": text}
    if tool == "jira_add_comment":
        args = {"ticket_id": "PLT-1", "body": text}
    elif tool == "jira_create_followup_ticket":
        args = {"parent_ticket_id": "PLT-1", "title": text, "description": text}
    return Draft(tool, args)


@pytest.mark.parametrize(
    "channel,allowed",
    [
        ("team-platform", True),
        ("announce", True),
        ("team-payments", False),
        ("other_team_channel", False),
        ("team-missing", False),
    ],
)
def test_recipients(channel, allowed):
    result = check_recipient_and_pii(
        draft(target=channel),
        requester=UserInfo("demo", "platform", 1),
        policy=load_team_policy(POLICY_PATH),
    )
    assert result.status == ("passed" if allowed else "blocked")
    assert result.reason == (None if allowed else "recipient_not_allowed")
    if not allowed:
        assert "자기 팀 채널" in result.message and "전사 공지" in result.message
        assert channel not in result.message


@pytest.mark.parametrize("text", PII)
@pytest.mark.parametrize(
    "team", ["HRM", "FIN", "LGL", "hr", "finance", "legal", "unknown"]
)
def test_blocking_policies(text, team):
    result = check_recipient_and_pii(
        draft(text, target="announce"),
        requester=UserInfo("demo", team, 1),
        policy=load_team_policy(POLICY_PATH),
    )
    assert result.status == "blocked"
    assert result.reason == "pii_blocked_by_team_policy"
    assert text not in result.message and team not in result.message
    assert "자기 팀 채널" in result.message and "전사 공지" in result.message


@pytest.mark.parametrize("text", PII)
@pytest.mark.parametrize(
    "tool", ["slack_send_message", "jira_add_comment", "jira_create_followup_ticket"]
)
@pytest.mark.parametrize("team", ["platform", "payments", "data-ai", "sre", "security"])
def test_masking(text, tool, team):
    original = draft(text, tool, "announce")
    requester = UserInfo("demo", team, 1)
    policy = load_team_policy(POLICY_PATH)
    result = check_recipient_and_pii(original, requester=requester, policy=policy)
    assert result.status == "masked" and result.reason == "pii_masked"
    assert result.message is None
    assert draft_sha256(original) != draft_sha256(result.draft)
    assert body_text(original).count(text) > 0
    assert not find_pii(body_text(result.draft))
    assert target_of(result.draft) == target_of(original)
    assert (
        check_recipient_and_pii(result.draft, requester=requester, policy=policy).status
        == "passed"
    )


def test_hash_and_targets():
    a = Draft("slack_send_message", {"channel": "announce", "text": "가"})
    b = Draft(
        "slack_send_message",
        {"text": unicodedata.normalize("NFD", "가"), "channel": "announce"},
    )
    assert draft_sha256(a) == draft_sha256(b)
    assert draft_sha256(a) != draft_sha256(draft("다름", target="announce"))
    assert target_of(draft()) == "team-platform"
    assert target_of(draft(tool="jira_add_comment")) == "PLT-1"
    assert (
        body_text(
            Draft(
                "jira_create_followup_ticket",
                {"parent_ticket_id": "PLT-1", "title": "제목", "description": None},
            )
        )
        == "제목"
    )
    with pytest.raises(ValueError):
        Draft("jira_get_ticket", {"ticket_id": "PLT-1"})


@pytest.mark.parametrize(
    "field", ["user", "user_id", "team", "grade", "requester", "actor"]
)
def test_identity_rejected(field):
    with pytest.raises(ValueError):
        Draft(
            "slack_send_message",
            {"channel": "announce", "text": "안내", field: "injected"},
        )
    with pytest.raises(ValueError):
        Draft(
            "slack_send_message",
            {"channel": "announce", "text": "안내", "nested": {field: "injected"}},
        )


def test_users_policy_and_audit(tmp_path):
    users = load_users()
    assert len(users) == 38
    assert users["demo-platform-g3"] == UserInfo("demo-platform-g3", "platform", 3)
    assert REASONS <= AUDIT_REASONS
    assert (
        team_policy_sha256(POLICY_PATH)
        == hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest()
    )
    path = tmp_path / "policy.json"
    path.write_text('{"platform": {"pii": "block"}}')
    assert load_team_policy(path).pii_for("platform") == "block"
    assert load_team_policy(path).pii_for("unknown") == "block"
    path.write_text('{"platform": {"pii": "allow"}}')
    with pytest.raises(ValueError):
        load_team_policy(path)


def test_draft_copies_arguments_and_policy_is_immutable():
    args = {"channel": "announce", "text": "합성 안내"}
    original = Draft("slack_send_message", args)
    before = draft_sha256(original)
    args["text"] = "변경"
    assert draft_sha256(original) == before
    with pytest.raises(TypeError):
        original.args["text"] = "변경"
    with pytest.raises(TypeError):
        load_team_policy(POLICY_PATH).teams["hr"] = "mask"


def test_user_loader_rejects_duplicate_and_extra_fields(tmp_path):
    path = tmp_path / "users.json"
    path.write_text(
        '{"users": [{"label": "demo", "team": "platform", "grade": 1}, {"label": "demo", "team": "platform", "grade": 2}]}'
    )
    with pytest.raises(ValueError):
        load_users(path)
    path.write_text(
        '{"users": [{"label": "demo", "team": "platform", "grade": 1, "extra": "invalid"}]}'
    )
    with pytest.raises(ValueError):
        load_users(path)


def test_account_normalization_and_masking():
    value = "１２３−４５６−７８９０１２"
    assert [match.kind for match in find_pii(value)] == ["account"]
    result = check_recipient_and_pii(
        draft(value),
        requester=UserInfo("demo", "platform", 1),
        policy=load_team_policy(POLICY_PATH),
    )
    assert result.status == "masked"
    assert not find_pii(body_text(result.draft))


def test_unknown_team_channel_is_not_a_real_recipient():
    result = check_recipient_and_pii(
        draft(target="team-HRM"),
        requester=UserInfo("demo", "HRM", 1),
        policy=load_team_policy(POLICY_PATH),
    )
    assert result.status == "blocked"
    assert result.reason == "recipient_not_allowed"
