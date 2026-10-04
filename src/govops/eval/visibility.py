"""Independent read permission oracle for evaluation metadata."""

from collections.abc import Mapping, Sequence
from typing import Any


def can_read(
    user: Mapping[str, Any],
    access_level: str,
    access_teams: Sequence[str],
    access_min_grade: int,
) -> bool:
    if access_level == "public":
        return True
    if user["team"] not in access_teams:
        return False
    if access_level == "team":
        return True
    return (
        access_level == "restricted"
        and type(access_min_grade) is int
        and user["grade"] >= access_min_grade
    )
