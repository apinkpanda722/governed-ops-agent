"""Trusted requester facts rendered from shared deterministic policy."""

from govops.agent.approval import ANNOUNCE_APPROVER_MIN_GRADE
from govops.agent.checks import allowed_channels
from govops.agent.org_info import team_name_ko
from govops.agent.users import UserInfo


def requester_section(user: UserInfo) -> str:
    """Include only the requester's team, destinations and approval conditions."""
    lines = [
        "## 요청자 정보",
        f"소속 팀: {team_name_ko(user.team)} (owner_team: {user.team})",
        "보낼 수 있는 Slack 채널:",
    ]
    lines.extend(
        f"- {channel}: {'전사 공지' if channel == 'announce' else '소속 팀 채널'}"
        for channel in allowed_channels(user.team)
    )
    lines.append(
        "전사 공지는 요청자 본인이 승인할 수 없으며, "
        f"같은 팀 {ANNOUNCE_APPROVER_MIN_GRADE}급 이상 사용자가 승인합니다."
    )
    return "\n".join(lines)
