"""Committed demo user labels, independent of credential storage."""

import json
from pathlib import Path


def load_users(path: Path | None = None) -> frozenset[str]:
    source = (
        path
        if path is not None
        else Path(__file__).resolve().parents[3] / "data/seed/users.json"
    )
    payload = json.loads(source.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("users"), list)
        or not payload["users"]
    ):
        raise ValueError("Invalid user seed")
    labels = set()
    for user in payload["users"]:
        if not isinstance(user, dict) or set(user) != {"label", "team", "grade"}:
            raise ValueError("Invalid user seed")
        if (
            not isinstance(user["label"], str)
            or not user["label"]
            or not isinstance(user["team"], str)
            or not user["team"]
            or type(user["grade"]) is not int
            or user["label"] in labels
        ):
            raise ValueError("Invalid user seed")
        labels.add(user["label"])
    return frozenset(labels)
