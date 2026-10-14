"""Redteam overlay preflight; never expose ticket bodies or command output."""

import hashlib
import json
import subprocess
from pathlib import Path

from govops.evalrun.dev import read_seed_matches

OVERLAY_RELATIVE = "data/redteam/overlay/jira_tickets.json"


def overlay_ticket_ids(root: Path) -> list[str]:
    try:
        payload = json.loads((root / OVERLAY_RELATIVE).read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(
            payload.get("tickets"), list
        ):
            raise ValueError("overlay_mismatch")  # noqa: TRY004 — fixed preflight code
        ids = []
        for ticket in payload["tickets"]:
            if (
                not isinstance(ticket, dict)
                or not isinstance(ticket.get("ticket_id"), str)
                or not ticket["ticket_id"]
            ):
                raise ValueError("overlay_mismatch")
            ids.append(ticket["ticket_id"])
        if len(set(ids)) != len(ids):
            raise ValueError("overlay_mismatch")
    except (OSError, UnicodeError, ValueError):
        raise ValueError("overlay_mismatch") from None
    return ids


def overlay_seed_check(root: Path, *, runner=subprocess.run) -> bool:
    """Compare the unchanged admin state contract with base plus overlay counts."""
    try:
        tickets = json.loads((root / "data/seed/tickets.json").read_text())["tickets"]
        users = json.loads((root / "data/seed/users.json").read_text())["users"]
        overlay_ids = overlay_ticket_ids(root)
        expected = [
            {
                "ticket_count": len(tickets) + len(overlay_ids),
                "comments": [],
                "children": [],
            },
            {
                "channels": [
                    {"channel_id": channel, "message_count": 0, "messages": []}
                    for channel in [
                        *(
                            f"team-{team}"
                            for team in sorted({u["team"] for u in users})
                        ),
                        "announce",
                    ]
                ]
            },
        ]
        actual = []
        for service in ("mock-jira", "mock-slack"):
            result = runner(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    ".env",
                    "-f",
                    "infra/docker-compose.yml",
                    "-f",
                    "infra/docker-compose.redteam.yml",
                    "exec",
                    "-T",
                    service,
                    "python",
                    "-m",
                    "govops.mocks.admin",
                    "state",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if result.returncode:
                return False
            actual.append(json.loads(result.stdout))

        def digest(value):
            return hashlib.sha256(
                json.dumps(value, sort_keys=True).encode()
            ).hexdigest()

        return digest(actual) == digest(expected)
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        KeyError,
        subprocess.SubprocessError,
    ):
        return False


async def read_overlay_matches(jira, base_tickets, overlay_tickets, users) -> bool:
    """Read base tickets as before, and overlay payloads as their known reporter."""
    try:
        if not await read_seed_matches(jira, base_tickets, users):
            return False
        base_ids = {ticket["ticket_id"] for ticket in base_tickets}
        overlay_ids = {ticket["ticket_id"] for ticket in overlay_tickets}
        if base_ids & overlay_ids:
            return False
        labels = {user["label"] for user in users}
        actual_ids = set(base_ids)
        for ticket in overlay_tickets:
            reporter = ticket["reporter_id"]
            if reporter not in labels:
                return False
            actual = await jira.get_ticket(reporter, ticket["ticket_id"])
            expected = ticket | {
                "parent_id": None,
                "metadata": {},
                "comments": ticket.get("comments", []),
                "children": [],
            }
            required = {"ticket_id", "comments", "children"} | (
                {"description"} if "description" in ticket else set()
            )
            if not isinstance(actual, dict) or not required <= actual.keys():
                return False
            if any(expected.get(key) != value for key, value in actual.items()):
                return False
            actual_ids.add(actual["ticket_id"])
        return actual_ids == base_ids | overlay_ids
    except Exception:  # noqa: BLE001 — read errors can contain private ticket bodies
        return False
