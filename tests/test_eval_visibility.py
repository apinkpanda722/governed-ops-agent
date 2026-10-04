import json
from pathlib import Path

from govops.eval.visibility import can_read
from govops.tools.smoke import expected_visible


def test_seed_oracle_agreement():
    users = json.loads(Path("data/seed/users.json").read_text())["users"]
    tickets = json.loads(Path("data/seed/tickets.json").read_text())["tickets"]
    assert len(users) == 38
    for user in users:
        for ticket in tickets:
            assert can_read(
                user,
                ticket["access_level"],
                ticket["access_teams"],
                ticket["access_min_grade"],
            ) == expected_visible(user, ticket)
