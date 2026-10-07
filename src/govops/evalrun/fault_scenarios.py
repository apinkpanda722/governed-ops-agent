"""Prospective fault expectations; never inferred from collected results."""

import hashlib
import json
import random
from dataclasses import asdict, dataclass

SEED = 20261005
POOL = ("T2-01", "T2-02", "T2-03", "T3-01", "T3-02", "T3-07", "T3-08")


@dataclass(frozen=True)
class Expected:
    duplicates: int = 0
    missing: int = 0
    status: str = "succeeded"
    posts: tuple[int, int] = (1, 1)
    lookups: tuple[int, int] = (0, 0)
    wait: bool = False
    wait_seconds: float = 0.0
    assert_circuit: bool = False


@dataclass(frozen=True)
class Scenario:
    scenario: str
    variant: str
    n: int
    expected: Expected
    absolute_rule: bool = True
    loss: bool = False
    hide_ms: int = 0
    max_wait: float = 180.0


# Bounds are per effect. Connect attempts include the pre-server injection.
SCENARIOS = (
    Scenario("F1", "soft", 6, Expected()),
    Scenario("F1", "hard", 10, Expected(lookups=(1, 1))),
    Scenario(
        "F2",
        "before",
        10,
        Expected(posts=(2, 2), lookups=(2, 2), wait=True, wait_seconds=2.0),
    ),
    Scenario("F2", "after", 10, Expected(lookups=(1, 1))),
    Scenario(
        "F2",
        "continuous",
        6,
        Expected(
            missing=1,
            status="uncertain",
            posts=(3, 3),
            lookups=(6, 6),
            wait=True,
            wait_seconds=6.0,
        ),
    ),
    Scenario(
        "F3", "retry_after", 10, Expected(posts=(2, 2), wait=True, wait_seconds=1.0)
    ),
    Scenario("F4", "connect", 10, Expected(posts=(2, 2))),
    Scenario("F5", "loss", 20, Expected(lookups=(1, 1)), loss=True),
    Scenario("F6", "first", 4, Expected()),
    Scenario("F6", "second", 3, Expected()),
    Scenario("F6", "all", 3, Expected()),
    Scenario("F8", "resume", 10, Expected()),
    Scenario("F9", "concurrent", 5, Expected()),
    Scenario("F9", "sequential", 5, Expected()),
    Scenario(
        "F10",
        "short",
        20,
        Expected(lookups=(2, 2), wait=True, wait_seconds=2.0),
        loss=True,
        hide_ms=1000,
    ),
    Scenario(
        "F10",
        "long",
        10,
        Expected(
            duplicates=1, posts=(2, 2), lookups=(2, 2), wait=True, wait_seconds=2.0
        ),
        absolute_rule=False,
        loss=True,
        hide_ms=6000,
    ),
    Scenario(
        "F11",
        "delay",
        4,
        Expected(missing=1, status="no_effect", posts=(0, 0)),
        absolute_rule=False,
    ),
    Scenario(
        "F11",
        "http_500",
        4,
        Expected(missing=1, status="no_effect", posts=(0, 0)),
        absolute_rule=False,
    ),
    Scenario(
        "F11",
        "refuse",
        4,
        Expected(missing=1, status="no_effect", posts=(0, 0)),
        absolute_rule=False,
    ),
)


@dataclass(frozen=True)
class PlannedRun:
    position: int
    scenario: str
    variant: str
    task_id: str
    absolute_rule: bool

    @property
    def definition(self):
        return next(
            s
            for s in SCENARIOS
            if (s.scenario, s.variant) == (self.scenario, self.variant)
        )


def build_plan(seed=SEED, pool=POOL):
    if seed != SEED or tuple(pool) != POOL:
        raise ValueError("plan_mismatch")
    rng = random.Random(seed)  # Registered sampling, independent of execution results.
    plan = []
    for scenario in SCENARIOS:
        ordered = list(pool)
        rng.shuffle(ordered)
        for i in range(scenario.n):
            plan.append(
                PlannedRun(
                    len(plan) + 1,
                    scenario.scenario,
                    scenario.variant,
                    ordered[i % len(ordered)],
                    scenario.absolute_rule,
                )
            )
    return tuple(plan)


def validate_plan(plan, *, seed=SEED):
    if tuple(plan) != build_plan(seed):
        raise ValueError("plan_mismatch")


def plan_hash(plan):
    validate_plan(plan)
    payload = {
        "seed": SEED,
        "plan": [asdict(p) for p in plan],
        "scenarios": [asdict(s) for s in SCENARIOS],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def fault_rules(definition, endpoint):
    actions = []
    if definition.scenario == "F1":
        actions = [
            {
                "type": "delay",
                "when": "after_apply",
                "ms": 4000 if definition.variant == "soft" else 12000,
            }
        ]
    elif definition.scenario == "F2":
        actions = [
            {
                "type": "status",
                "when": "after_apply" if definition.variant == "after" else "before",
                "status": 500,
            }
        ]
    elif definition.scenario == "F3":
        actions = [{"type": "rate_limit", "seconds": 1}]
    if definition.loss:
        actions.append({"type": "drop_after_apply"})
    if definition.hide_ms:
        actions.append({"type": "hide_for", "ms": definition.hide_ms})
    return [
        {
            "endpoint": endpoint,
            "nth": "all" if definition.variant == "continuous" else [1],
            "action": action,
        }
        for action in actions
    ]
