"""Keep presentation constants synchronized with the Python contracts."""

import re
from pathlib import Path

from govops.agent.org_info import TEAMS
from govops.tools.registry import tool_specs

ROOT = Path(__file__).resolve().parents[1]


def test_web_teams_match():
    source = (ROOT / "web/src/lib/teams.ts").read_text()
    pairs = re.findall(r'"([a-z-]+)":\s*"([^"]+)"', source)
    assert pairs
    assert len(pairs) == len(TEAMS)
    assert dict(pairs) == {team.owner_team: team.name_ko for team in TEAMS}


def test_web_tool_labels_match():
    source = (ROOT / "web/src/lib/messages.ts").read_text()
    specs = tool_specs()
    for function, side_effect in (("toolLabel", False), ("sentResultLabel", True)):
        body = source.split(f"export function {function}(", 1)[1].split("\n}", 1)[0]
        names = re.findall(r'"([a-z_]+)":\s*"[^"]+"', body)
        assert names
        assert len(names) == len(set(names))
        assert set(names) == {
            name for name, spec in specs.items() if spec.side_effect == side_effect
        }


def test_web_max_steps_text_matches_python():
    from govops.agent.graph import MAX_STEPS

    source = (ROOT / "web/src/lib/messages.ts").read_text()
    assert f"조사 단계 한도({MAX_STEPS}단계)에 도달했습니다." in source


def test_web_approved_run_notice_matches_graph():
    notice = "승인된 내용대로 실행했습니다"
    graph = (ROOT / "src/govops/agent/graph.py").read_text()
    web = (ROOT / "web/src/lib/messages.ts").read_text()
    assert f'"completed": "{notice}"' in graph
    assert f'APPROVED_RUN_NOTICE = "{notice}"' in web


def test_web_delete_confirm_notice_matches_approved_design():
    notice = (
        "대화를 삭제할까요? 원문은 다음 정리 때 제거되고, "
        "감사 기록과 이미 발송된 효과는 남는다."
    )
    web = (ROOT / "web/src/lib/messages.ts").read_text()
    assert f'DELETE_CONFIRM_NOTICE = "{notice}"' in web
    design = (ROOT / "docs/M6_DESIGN_DRAFT.md").read_text()
    assert notice.split("? ", 1)[1] in design


def test_web_limited_notice_steps_match_python():
    from govops.agent.graph import MAX_STEPS

    source = (ROOT / "web/src/lib/messages.ts").read_text()
    assert f"조사 단계 한도({MAX_STEPS}단계)에서 멈추고" in source
