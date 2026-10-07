"""Load ticket seed fields without retaining extra source data."""

import json
from pathlib import Path

TICKET_FIELDS = frozenset(
    {
        "ticket_id",
        "project_key",
        "owner_team",
        "title",
        "ticket_type",
        "status",
        "priority",
        "service",
        "incident_id",
        "reporter_id",
        "assignee_id",
        "created_at",
        "resolved_at",
        "story_points",
        "access_level",
        "access_teams",
        "access_min_grade",
    }
)
NULLABLE_FIELDS = {"incident_id", "resolved_at", "story_points"}


def load_tickets(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("tickets"), list):
        raise ValueError("ticket seed must contain a tickets list")  # noqa: TRY004
    tickets = []
    ids = set()
    for raw in payload["tickets"]:
        if not isinstance(raw, dict) or not TICKET_FIELDS <= raw.keys():
            raise ValueError("ticket seed is missing required fields")
        ticket = {field: raw[field] for field in TICKET_FIELDS}
        for field in TICKET_FIELDS - {
            "access_teams",
            "access_min_grade",
            "story_points",
        }:
            value = ticket[field]
            if value is None and field in NULLABLE_FIELDS:
                continue
            if not isinstance(value, str):
                raise ValueError("ticket seed string field is invalid")  # noqa: TRY004
        ticket_id = ticket["ticket_id"]
        prefix, _, number = ticket_id.rpartition("-")
        if (
            prefix != ticket["project_key"]
            or not number.isascii()
            or not number.isdigit()
            or not prefix
        ):
            raise ValueError("ticket ID must be project key followed by a number")
        if ticket_id in ids:
            raise ValueError("ticket seed contains duplicate ticket IDs")
        if ticket["access_level"] not in {"public", "team", "restricted"}:
            raise ValueError("unknown ticket access level")
        teams = ticket["access_teams"]
        if not isinstance(teams, list) or not all(
            isinstance(team, str) for team in teams
        ):
            raise ValueError("ticket access teams must be a string list")
        if ticket["access_level"] == "public" and teams:
            raise ValueError("public ticket access teams must be empty")
        if type(ticket["access_min_grade"]) is not int:
            raise ValueError("ticket minimum grade must be an integer")
        points = ticket["story_points"]
        if points is not None and (type(points) not in {int, float}):
            raise ValueError("ticket story points must be numeric or null")
        ids.add(ticket_id)
        tickets.append(ticket)
    return tickets


def merge_overlay(base: list[dict], overlay_path: Path | None) -> list[dict]:
    """Append validated optional tickets, without changing the seed loader contract."""
    from copy import deepcopy

    if overlay_path is None:
        return deepcopy(base)
    overlay = load_tickets(overlay_path)
    if {t["ticket_id"] for t in base} & {t["ticket_id"] for t in overlay}:
        raise ValueError("overlay ticket IDs collide with seed")
    raw = json.loads(overlay_path.read_text(encoding="utf-8"))["tickets"]
    for ticket, source in zip(overlay, raw, strict=True):
        if "description" in source:
            if not isinstance(source["description"], str):
                raise ValueError("overlay description must be a string")
            ticket["description"] = source["description"]
        comments = source.get("comments", [])
        if not isinstance(comments, list):
            raise ValueError("overlay comments must be a list")  # noqa: TRY004
        for comment in comments:
            if (
                not isinstance(comment, dict)
                or set(comment) != {"body", "author"}
                or not all(isinstance(v, str) for v in comment.values())
            ):
                raise ValueError("overlay comment fields are invalid")
        ticket["comments"] = deepcopy(comments)
    return deepcopy(base) + overlay
