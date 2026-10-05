"""Deterministic recipient and outbound PII checks before approval."""

from dataclasses import dataclass
from typing import Literal

from govops.agent.draft import Draft, body_text, target_of
from govops.agent.users import UserInfo
from govops.guardrails.pii import find_pii, mask_pii
from govops.guardrails.policy import TeamPolicy

# Public mock channel inventory; no dependency on the mock service.
_CHANNEL_TEAMS = frozenset(
    {"platform", "payments", "data-ai", "sre", "security", "hr", "finance", "legal"}
)

RECIPIENT_NOT_ALLOWED = "recipient_not_allowed"
PII_BLOCKED_BY_TEAM_POLICY = "pii_blocked_by_team_policy"
PII_MASKED = "pii_masked"
REASONS = frozenset({RECIPIENT_NOT_ALLOWED, PII_BLOCKED_BY_TEAM_POLICY, PII_MASKED})


@dataclass(frozen=True)
class CheckResult:
    status: Literal["passed", "masked", "blocked"]
    reason: str | None
    draft: Draft
    message: str | None


def check_recipient_and_pii(
    draft: Draft, *, requester: UserInfo, policy: TeamPolicy
) -> CheckResult:
    if draft.tool == "slack_send_message" and (
        target_of(draft) not in {"announce", f"team-{requester.team}"}
        or (target_of(draft) != "announce" and requester.team not in _CHANNEL_TEAMS)
    ):
        return CheckResult(
            "blocked",
            RECIPIENT_NOT_ALLOWED,
            draft,
            "허용되지 않은 수신처라 보낼 수 없습니다. 자기 팀 채널과 전사 공지로만 보낼 수 있습니다.",
        )
    if not find_pii(body_text(draft)):
        return CheckResult("passed", None, draft, None)
    if policy.pii_for(requester.team) == "block":
        return CheckResult(
            "blocked",
            PII_BLOCKED_BY_TEAM_POLICY,
            draft,
            "개인정보가 포함되어 팀 정책에 따라 보낼 수 없습니다. 개인정보를 제거해 주세요. 보낼 수 있는 곳은 자기 팀 채널과 전사 공지입니다.",
        )
    fields = {
        "slack_send_message": ("text",),
        "jira_add_comment": ("body",),
        "jira_create_followup_ticket": ("title", "description"),
    }[draft.tool]
    args = dict(draft.args)
    for field in fields:
        if isinstance(args.get(field), str):
            args[field] = mask_pii(args[field])
    return CheckResult("masked", PII_MASKED, Draft(draft.tool, args), None)
