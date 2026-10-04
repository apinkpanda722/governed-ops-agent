"""Quadratic weighted Cohen kappa and seeded percentile bootstrap intervals."""

import math
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import mean
from typing import Literal

from govops.eval.grading import DIMENSIONS, RubricScore

BOOTSTRAP_SEED = 20261005
BOOTSTRAP_RESAMPLES = 10_000
CI_LEVEL = 0.95
THRESHOLD = 0.6
FALLBACK_LABEL = "채점 모델 검증 미달, 사람 표본(n=30) 기반"


def weighted_kappa(
    a: Sequence[int], b: Sequence[int], *, categories: Sequence[int] = (0, 1, 2, 3)
) -> float | None:
    if (
        len(a) != len(b)
        or len(categories) < 2
        or len(set(categories)) != len(categories)
    ):
        raise ValueError("invalid kappa inputs")
    if any(type(x) is not int or x not in categories for x in [*a, *b]):
        raise ValueError("invalid kappa category")
    if not a or len(set(a)) < 2 or len(set(b)) < 2:
        return None
    positions = {x: i for i, x in enumerate(categories)}
    n = len(a)
    ca, cb = Counter(a), Counter(b)
    # The common (k-1)^2 denominator cancels in observed / expected.
    observed = (
        sum((positions[x] - positions[y]) ** 2 for x, y in zip(a, b, strict=True)) / n
    )
    expected = (
        sum(ca[x] * cb[y] * (positions[x] - positions[y]) ** 2 for x in ca for y in cb)
        / n**2
    )
    return 1 - observed / expected if expected else None


def _percentiles(values):
    values = sorted(values)

    def percentile(p):
        position = (len(values) - 1) * p
        lo = math.floor(position)
        hi = math.ceil(position)
        return values[lo] + (values[hi] - values[lo]) * (position - lo)

    tail = (1 - CI_LEVEL) / 2
    return percentile(tail), percentile(1 - tail)


def bootstrap_ci(values: Sequence[float], *, stat=mean) -> tuple[float, float]:
    if not values or any(not math.isfinite(v) for v in values):
        raise ValueError("invalid bootstrap inputs")
    rng = random.Random(BOOTSTRAP_SEED)
    return _percentiles(
        [stat(rng.choices(values, k=len(values))) for _ in range(BOOTSTRAP_RESAMPLES)]
    )


@dataclass(frozen=True)
class DimensionResult:
    kappa: float | None
    kappa_ci: tuple[float, float] | None
    trusted: bool
    human_mean: float
    human_mean_ci: tuple[float, float]
    label: str
    kappa_resamples_undefined: int = 0


@dataclass(frozen=True)
class KappaReport:
    per_dimension: dict[str, DimensionResult]
    n: int
    threshold: float


def kappa_report(
    human: Mapping[str, RubricScore], gemini: Mapping[str, RubricScore]
) -> KappaReport:
    if len(human) != 30 or not human.keys() <= gemini.keys():
        raise ValueError("kappa requires 30 matched human samples")
    ids = sorted(human)
    result = {}
    for dimension in DIMENSIONS:
        a = [getattr(human[key], dimension) for key in ids]
        b = [getattr(gemini[key], dimension) for key in ids]
        kappa = weighted_kappa(a, b)
        rng = random.Random(BOOTSTRAP_SEED)
        draws = []
        for _ in range(BOOTSTRAP_RESAMPLES):
            indices = rng.choices(range(30), k=30)
            value = weighted_kappa([a[i] for i in indices], [b[i] for i in indices])
            if value is not None:
                draws.append(value)
        trusted = kappa is not None and kappa >= THRESHOLD
        result[dimension] = DimensionResult(
            kappa,
            _percentiles(draws) if draws else None,
            trusted,
            mean(a),
            bootstrap_ci(a),
            "Gemini 채점 검증 통과" if trusted else FALLBACK_LABEL,
            BOOTSTRAP_RESAMPLES - len(draws),
        )
    return KappaReport(result, 30, THRESHOLD)


def final_dimension_source(
    report: KappaReport,
) -> dict[str, Literal["gemini", "human_sample_only"]]:
    return {
        d: "gemini" if value.trusted else "human_sample_only"
        for d, value in report.per_dimension.items()
    }
