"""조직과 티켓 스키마의 고정 값으로 렌더하는 프롬프트 절."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TeamInfo:
    name_ko: str
    owner_team: str
    project_key: str


TEAMS: tuple[TeamInfo, ...] = (
    TeamInfo("플랫폼개발팀", "platform", "PLT"),
    TeamInfo("결제서비스팀", "payments", "PAY"),
    TeamInfo("데이터AI팀", "data-ai", "DAI"),
    TeamInfo("SRE인프라팀", "sre", "SRE"),
    TeamInfo("정보보안팀", "security", "SEC"),
    TeamInfo("인사팀", "hr", "HRM"),
    TeamInfo("재무팀", "finance", "FIN"),
    TeamInfo("법무컴플라이언스팀", "legal", "LGL"),
)
TICKET_STATUS: tuple[str, ...] = ("open", "in_progress", "resolved", "closed")
TICKET_PRIORITY: tuple[str, ...] = ("critical", "high", "medium", "low")
TICKET_TYPE: tuple[str, ...] = ("incident", "task", "request", "improvement")


def org_section() -> str:
    """팀 대응과 티켓 열거형 값만 담는 짧은 한국어 절."""
    lines = ["## 조직 정보", "팀 이름 = owner_team = project_key"]
    lines.extend(
        f"{team.name_ko} = {team.owner_team} = {team.project_key}" for team in TEAMS
    )
    statuses = "·".join(
        f"{status}(진행 중)" if status == "in_progress" else status
        for status in TICKET_STATUS
    )
    lines.extend(
        (
            f"티켓 status: {statuses}",
            f"티켓 priority: {'·'.join(TICKET_PRIORITY)}",
            f"티켓 ticket_type: {'·'.join(TICKET_TYPE)}",
            (
                "`run_sql`의 `tickets`·`approvals` 조건과 `jira_search_tickets`의 "
                "`project`·`status`에는 한글 팀 이름이 아니라 영문 코드 값을 쓴다."
            ),
        )
    )
    return "\n".join(lines) + "\n"
