"""M1 decisions stay aligned with contracts and milestone boundaries."""

import re
from pathlib import Path

from govops.tools.registry import tool_specs

ROOT = Path(__file__).resolve().parents[1]
TOOL_NAMES = {
    "search_docs",
    "lookup_id",
    "run_sql",
    "get_document",
    "jira_get_ticket",
    "jira_search_tickets",
    "jira_add_comment",
    "jira_create_followup_ticket",
    "slack_send_message",
}


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_adr_and_tool_table_match_registry():
    adr = read("docs/ADR.md").split("### ADR-017:", 1)[1]
    assert all(f"`{name}`" in adr for name in TOOL_NAMES)
    architecture = read("docs/ARCHITECTURE.md")
    table = architecture.split("## 도구와 부작용·멱등성", 1)[1].split("\n- ", 1)[0]
    assert (
        set(re.findall(r"^\| `([^`]+)` \|", table, re.MULTILINE))
        == (set(tool_specs()))
        == TOOL_NAMES
    )


def test_milestone_approval_boundary():
    prd = read("docs/PRD.md")
    m3 = next(line for line in prd.splitlines() if line.startswith("| M3 |"))
    m5 = next(line for line in prd.splitlines() if line.startswith("| M5 |"))
    assert "approval" in m3 or "승인" in m3
    assert "interrupt" not in m5 and "approval" not in m5
    assert "승인 노드" not in m5


def test_agent_critical_count_and_data_directories():
    assert read("AGENTS.md").count("CRITICAL:") == 25
    structure = read("docs/ARCHITECTURE.md").split("## 디렉토리 구조", 1)[1]
    structure = structure.split("## 배치도", 1)[0]
    assert all(f"{name}/" in structure for name in ("contract", "seed", "replay"))


def test_jira_existence_privacy_and_slack_denial():
    adr = read("docs/ADR.md")
    m1 = adr.split("### ADR-017:", 1)[1]
    assert "mock-jira" in m1 and "404" in m1 and "쓰기 권한" in m1
    mock = adr.split("### ADR-006:", 1)[1].split("### ADR-007:", 1)[0]
    assert '쓰기 거부는 명시적 "권한 없음"' not in mock
    assert "mock-slack 채널" in mock and "403" in mock and "404" in mock


def test_agent_phase_commands_and_ledger():
    commands = read("AGENTS.md").split("## 명령어", 1)[1]
    for name in (
        "costs-init",
        "agent-key",
        "db-init",
        "serve",
        "ask",
        "smoke-checkpointer",
        "smoke-agent",
    ):
        assert f"make {name} " in commands
    assert read("AGENTS.md").count("CRITICAL:") == 25
    assert "data/manifest/costs.jsonl" in read("docs/ARCHITECTURE.md")
