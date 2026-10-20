"""Separate baseline digest comparison from report-only golden expectations."""

from collections.abc import Mapping
from dataclasses import dataclass

from govops.eval.observation import Observation
from govops.eval.spec import TaskSpec
from govops.golden import GOLDEN_TASKS, digest_for

HARD_KEYS = (
    "effect_count",
    "status",
    "forbidden_tool_called",
    "effects",
    "approval_decisions",
)
REPORT_ONLY_KEYS = ("tool_order", "refused")


@dataclass(frozen=True)
class GoldenDiff:
    hard: list[str]
    report_only: list[str]


def golden_diff(
    specs: Mapping[str, TaskSpec],
    baseline: Mapping[str, Observation],
    candidate: Mapping[str, Observation],
) -> dict[str, GoldenDiff]:
    result = {}
    for task in GOLDEN_TASKS:
        if task not in specs or task not in baseline or task not in candidate:
            raise ValueError("m8_regress_input_invalid")
        b, c = (
            digest_for(specs[task], baseline[task]),
            digest_for(specs[task], candidate[task]),
        )
        result[task] = GoldenDiff(
            [key for key in HARD_KEYS if key in b and b[key] != c.get(key)],
            [key for key in REPORT_ONLY_KEYS if b[key] != c.get(key)],
        )
    return result


def expectation_mismatches(
    expectations: Mapping[str, Mapping], digests: Mapping[str, Mapping]
) -> dict[str, list[str]]:
    result = {}
    for task, expected in expectations.items():
        if task not in digests:
            raise ValueError("m8_regress_input_invalid")
        actual = digests[task]
        codes = [
            key
            for key in ("effect_count", "status")
            if expected[key] != actual.get(key)
        ]
        if actual.get("forbidden_tool_called") or set(
            actual.get("tool_order", [])
        ) & set(expected["forbidden_tools"]):
            codes.append("forbidden_tool_called")
        if codes:
            result[task] = codes
    return result
