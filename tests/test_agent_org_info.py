"""Organization schema facts come from seeds, not evaluation answers."""

import json
import re
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from govops.agent import org_info
from govops.env import env_or

ROOT = Path(__file__).resolve().parents[1]


def test_org_constants_match_seeds():
    tickets = json.loads((ROOT / "data/seed/tickets.json").read_text())["tickets"]
    users = json.loads((ROOT / "data/seed/users.json").read_text())["users"]
    assert isinstance(org_info.TEAMS, tuple)
    assert len(org_info.TEAMS) == 8
    pairs = {(team.owner_team, team.project_key) for team in org_info.TEAMS}
    assert len(pairs) == 8
    assert pairs == {
        (ticket["owner_team"], ticket["project_key"]) for ticket in tickets
    }
    codes = {team.owner_team for team in org_info.TEAMS}
    assert codes == {ticket["owner_team"] for ticket in tickets}
    assert codes == {user["team"] for user in users}
    for field, values in (
        ("status", org_info.TICKET_STATUS),
        ("priority", org_info.TICKET_PRIORITY),
        ("ticket_type", org_info.TICKET_TYPE),
    ):
        assert isinstance(values, tuple)
        assert len(values) == len(set(values)) == 4
        assert set(values) == {ticket[field] for ticket in tickets}


def test_team_info_is_frozen():
    team = org_info.TeamInfo("플랫폼개발팀", "platform", "PLT")
    with pytest.raises(FrozenInstanceError):
        team.owner_team = "changed"


def test_org_section_contains_schema_only():
    section = org_info.org_section()
    assert section == org_info.org_section()
    assert "조직 정보" in section
    assert not re.search(r"[0-9]", section)
    assert not re.search(r"\b(?:SELECT|COUNT)\b", section, re.IGNORECASE)
    for team in org_info.TEAMS:
        assert all(
            value in section
            for value in (team.name_ko, team.owner_team, team.project_key)
        )
    for values in (
        org_info.TICKET_STATUS,
        org_info.TICKET_PRIORITY,
        org_info.TICKET_TYPE,
    ):
        assert all(value in section for value in values)
    assert "in_progress(진행 중)" in section
    assert all(
        value in section
        for value in ("owner_team", "project_key", "status", "priority", "ticket_type")
    )
    assert (
        "`run_sql`의 `tickets`·`approvals` 조건과 `jira_search_tickets`의 "
        "`project`·`status`에는 한글 팀 이름이 아니라 영문 코드 값을 쓴다"
    ) in section


def test_org_section_renders_constants(monkeypatch):
    monkeypatch.setattr(
        org_info, "TEAMS", (org_info.TeamInfo("시험팀", "sample", "SMP"),)
    )
    monkeypatch.setattr(org_info, "TICKET_STATUS", ("sample_status",))
    monkeypatch.setattr(org_info, "TICKET_PRIORITY", ("sample_priority",))
    monkeypatch.setattr(org_info, "TICKET_TYPE", ("sample_type",))
    section = org_info.org_section()
    assert all(
        value in section
        for value in (
            "시험팀",
            "sample",
            "SMP",
            "sample_status",
            "sample_priority",
            "sample_type",
        )
    )
    assert "플랫폼개발팀" not in section
    assert "critical" not in section
    assert "incident" not in section
    assert section == org_info.org_section()


def test_korean_team_names_match_a_source():
    a_root = Path(
        env_or("A_ROOT", "~/PycharmProjects/ko-docs-agentic-rag")
    ).expanduser()
    source = a_root / "src/kodocs/synth/templates/system.md"
    if not source.is_file():
        pytest.skip("A organization source is not available")
    first_line = source.read_text(encoding="utf-8").splitlines()[0]
    assert all(team.name_ko in first_line for team in org_info.TEAMS)
