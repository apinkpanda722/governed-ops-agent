"""File-backed team PII policy; unknown teams fail closed."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

PiiPolicy = Literal["block", "mask"]


@dataclass(frozen=True)
class TeamPolicy:
    teams: Mapping[str, PiiPolicy]

    def __post_init__(self) -> None:
        if any(
            not isinstance(team, str) or mode not in {"block", "mask"}
            for team, mode in self.teams.items()
        ):
            raise ValueError("Invalid team policy")
        object.__setattr__(self, "teams", MappingProxyType(dict(self.teams)))

    def pii_for(self, team: str) -> PiiPolicy:
        return self.teams.get(team, "block")


def load_team_policy(path: str | Path) -> TeamPolicy:
    raw = json.loads(Path(path).read_bytes())
    if not isinstance(raw, dict) or any(
        not isinstance(value, dict) or set(value) != {"pii"} for value in raw.values()
    ):
        raise ValueError("Invalid team policy file")
    return TeamPolicy({team: value["pii"] for team, value in raw.items()})


def team_policy_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
