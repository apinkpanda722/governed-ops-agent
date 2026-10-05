"""Non-secret requester identity values loaded from the user seed."""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UserInfo:
    label: str
    team: str
    grade: int

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label:
            raise ValueError("Invalid user label")
        if not isinstance(self.team, str) or not self.team:
            raise ValueError("Invalid user team")
        if type(self.grade) is not int or self.grade < 1:
            raise ValueError("Invalid user grade")


def load_users(path: str | Path = "data/seed/users.json") -> dict[str, UserInfo]:
    raw = json.loads(Path(path).read_bytes())
    if (
        not isinstance(raw, dict)
        or set(raw) != {"users"}
        or not isinstance(raw["users"], list)
    ):
        raise ValueError("Invalid user seed")
    users = {}
    for row in raw["users"]:
        if not isinstance(row, dict) or set(row) != {"label", "team", "grade"}:
            raise ValueError("Invalid user record")
        user = UserInfo(**row)
        if user.label in users:
            raise ValueError("Duplicate user label")
        users[user.label] = user
    return users
