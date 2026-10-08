"""Turn-local exploration budget using the registered structural definition."""

from dataclasses import dataclass, field

from govops.env import env_or

EXPLORATION_N = 4
EXPLORATION_ANSWER = "요청하신 정보를 찾을 수 없습니다."
STOP_REASON = "exploration_exhausted"


def identifiers(value):
    found = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"doc_id", "key", "ticket_id"} and isinstance(child, str):
                found.add(child)
            elif isinstance(child, (dict, list)):
                found.update(identifiers(child))
    elif isinstance(value, list):
        for child in value:
            found.update(identifiers(child))
    return found


def longest_streak(calls):
    seen = set()
    streak = maximum = 0
    for call in calls:
        ids = identifiers(call.get("result", {}))
        streak = 0 if ids - seen else streak + 1
        seen.update(ids)
        maximum = max(maximum, streak)
    return maximum


@dataclass
class ExplorationCounter:
    seen: set[str] = field(default_factory=set)
    streak: int = 0

    def observe(self, payload) -> int:
        ids = identifiers(payload)
        self.streak = 0 if ids - self.seen else self.streak + 1
        self.seen |= ids
        return self.streak

    @classmethod
    def from_state(cls, state):
        return cls(
            set(state.get("explored_ids", [])), state.get("no_new_info_streak", 0)
        )

    def to_state(self) -> dict:
        return {"explored_ids": sorted(self.seen), "no_new_info_streak": self.streak}

    def exhausted(self, n: int) -> bool:
        return self.streak >= n


def exploration_budget_from_env() -> int | None:
    value = env_or("GOVOPS_EXPLORATION_BUDGET", "")
    if value in {"", "1"}:
        return EXPLORATION_N
    if value == "0":
        return None
    raise ValueError("invalid exploration budget flag")
