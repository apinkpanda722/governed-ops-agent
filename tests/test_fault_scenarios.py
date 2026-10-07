"""Preregistered counts and deterministic sampling, without external services."""

from collections import Counter

import pytest

from govops.evalrun.fault_scenarios import POOL, SCENARIOS, build_plan, validate_plan


def test_plan_counts_and_determinism():
    plan = build_plan()
    assert plan == build_plan()
    assert len(plan) == 154
    assert sum(row.absolute_rule for row in plan) == 132
    assert {row.task_id for row in plan} == set(POOL)
    assert Counter(row.scenario for row in plan) == {
        "F1": 16,
        "F2": 26,
        "F3": 10,
        "F4": 10,
        "F5": 20,
        "F6": 10,
        "F8": 10,
        "F9": 10,
        "F10": 30,
        "F11": 12,
    }
    assert [s.n for s in SCENARIOS if s.scenario == "F6"] == [4, 3, 3]
    validate_plan(plan)


@pytest.mark.parametrize("mutation", ["missing", "order", "sample"])
def test_unregistered_plan_refused(mutation):
    plan = list(build_plan())
    if mutation == "missing":
        plan.pop()
    elif mutation == "order":
        plan.reverse()
    else:
        from dataclasses import replace

        plan[0] = replace(plan[0], task_id="T2-04")
    with pytest.raises(ValueError, match="plan_mismatch"):
        validate_plan(plan)


def test_expectations_and_wait_budget():
    for scenario in SCENARIOS:
        assert scenario.expected.status
        assert scenario.expected.posts[0] <= scenario.expected.posts[1]
        assert scenario.max_wait > 3 * 30 + 2 * 4
    continuous = next(s for s in SCENARIOS if s.variant == "continuous")
    assert continuous.expected.status == "uncertain"
    assert continuous.expected.missing == 1
    assert not continuous.expected.assert_circuit
    for s in SCENARIOS:
        if s.scenario == "F10":
            assert s.loss and s.hide_ms in (1000, 6000)
