"""Check the redteam override without starting compose or reading payloads."""

from pathlib import Path, PurePosixPath

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_redteam_override_has_only_jira_mount_and_environment():
    text = (ROOT / "infra/docker-compose.redteam.yml").read_text(encoding="utf-8")
    override = yaml.safe_load(text)
    assert set(override) == {"services"}
    assert set(override["services"]) == {"mock-jira"}
    jira = override["services"]["mock-jira"]
    assert set(jira) == {"volumes", "environment"}
    assert jira["volumes"] == ["../data/redteam:/app/data/redteam:ro"]
    assert jira["environment"] == {
        "MOCK_JIRA_OVERLAY": "/app/data/redteam/overlay/jira_tickets.json"
    }
    mount = PurePosixPath(jira["volumes"][0].split(":")[1])
    assert PurePosixPath(jira["environment"]["MOCK_JIRA_OVERLAY"]).is_relative_to(mount)
    assert (
        "docker compose --env-file .env -f infra/docker-compose.yml "
        "-f infra/docker-compose.redteam.yml up -d"
    ) in text
    assert "레드팀 실행 전용, 일반 평가는 쓰지 않는다" in text


def test_base_compose_has_no_redteam_configuration():
    text = (ROOT / "infra/docker-compose.yml").read_text(encoding="utf-8")
    assert "MOCK_JIRA_OVERLAY" not in text
    assert "data/redteam" not in text
