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
