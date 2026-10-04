import pytest

from govops.eval.grading import RubricScore
from govops.eval.kappa import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    CI_LEVEL,
    bootstrap_ci,
    final_dimension_source,
    kappa_report,
    weighted_kappa,
)


def test_known_values():
    assert weighted_kappa([0, 1, 2, 3], [0, 1, 2, 3]) == 1
    # observed squared disagreement=1/3; expected=5/3 => 0.8
    assert weighted_kappa([0, 1, 2], [0, 2, 2]) == pytest.approx(0.8)
    assert weighted_kappa([0, 1, 2, 3], [3, 2, 1, 0]) < 0
    assert weighted_kappa([1, 1], [0, 3]) is None
    assert weighted_kappa([], []) is None
    with pytest.raises(ValueError):
        weighted_kappa([4], [0])


def test_bootstrap():
    assert (BOOTSTRAP_SEED, BOOTSTRAP_RESAMPLES, CI_LEVEL) == (20261005, 10000, 0.95)
    assert bootstrap_ci([2] * 30) == (2, 2)
    ci = bootstrap_ci([0, 1, 2, 3])
    assert ci == bootstrap_ci([0, 1, 2, 3])
    assert ci[0] <= 1.5 <= ci[1]


def test_report():
    human = {str(i): RubricScore(i % 4, 1, i % 4) for i in range(30)}
    gemini = {str(i): RubricScore(i % 4, 3, 3 - i % 4) for i in range(30)}
    report = kappa_report(human, gemini)
    assert report.n == 30 and report.threshold == 0.6
    assert report.per_dimension["correctness"].kappa == 1
    assert report.per_dimension["correctness"].kappa_ci == (1, 1)
    bad = report.per_dimension["groundedness"]
    assert bad.kappa is None and not bad.trusted
    assert bad.label == "채점 모델 검증 미달, 사람 표본(n=30) 기반"
    assert bad.human_mean == 1 and bad.human_mean_ci == (1, 1)
    assert bad.kappa_resamples_undefined == 10000
    assert final_dimension_source(report) == {
        "correctness": "gemini",
        "groundedness": "human_sample_only",
        "format": "human_sample_only",
    }
