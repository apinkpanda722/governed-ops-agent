"""Hand-calculated labels and seeded clusters; no external observations."""

import itertools
import random

import pytest

from govops.eval.kappa import weighted_kappa
from govops.grounding import metrics
from govops.grounding.grader import RowAggregate
from govops.grounding.inject import InjectedRow, InjectionSkip


def row(human, model=None, key="r", positives=None):
    human = {f"s{i}": label for i, label in enumerate(human, 1)}
    model = (
        dict(human)
        if model is None
        else {f"s{i}": label for i, label in enumerate(model, 1)}
    )
    return metrics.RowLabels(
        key,
        human,
        model,
        positives
        if positives is not None
        else {
            sid: label in {"unsupported", "contradicted"}
            for sid, label in model.items()
        },
    )


@pytest.mark.parametrize(
    "model,expected",
    [
        (["supported", "supported", "unsupported", "contradicted"], 1.0),
        (["unsupported", "contradicted", "supported", "not_claim"], -1.0),
        (["supported", "unsupported", "not_claim", "contradicted"], 0.0),
    ],
)
def test_sentence_kappa_hand_calculation(model, expected):
    rows = [row(["supported", "not_claim", "unsupported", "contradicted"], model)]
    assert metrics.sentence_kappa(rows) == pytest.approx(expected)


def test_kappa_pools_every_sentence_and_uses_binary_repeat_majority():
    rows = [
        row(["supported"] * 9, key="a"),
        row(["unsupported"], ["supported"], key="b"),
    ]
    assert metrics.sentence_kappa(rows) is None
    mixed = row(
        ["supported", "unsupported"],
        ["unsupported", "unsupported"],
        positives={"s1": False, "s2": True},
    )
    assert metrics.sentence_kappa([mixed]) == 1
    matrix = metrics.confusion_matrix([mixed])
    assert matrix["supported"]["unsupported"] == 1


def test_unequal_row_sizes_pool_sentences_instead_of_averaging_rows():
    rows = [
        row(["supported"] * 8, ["supported"] * 6 + ["unsupported"] * 2, key="a"),
        row(["unsupported"] * 2, key="b"),
    ]
    # Disagreement .2, expected disagreement .8*.4 + .2*.6 = .44.
    assert metrics.sentence_kappa(rows) == pytest.approx(1 - 0.2 / 0.44)


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [row(["supported"])],
        [row(["unsupported", "supported"], ["supported", "not_claim"])],
        [row(["supported", "not_claim"], ["supported", "unsupported"])],
    ],
)
def test_degenerate_kappa(rows):
    assert metrics.sentence_kappa(rows) is None


@pytest.mark.parametrize(
    "function",
    [
        metrics.sentence_kappa,
        metrics.cluster_bootstrap_ci,
        metrics.confusion_matrix,
        metrics.precision_recall,
        metrics.row_level_kappa,
    ],
)
def test_missing_sentence_rejected(function):
    with pytest.raises(ValueError):
        function(
            [
                metrics.RowLabels(
                    "r", {"s1": "supported"}, {"s2": "supported"}, {"s2": False}
                )
            ]
        )


def test_missing_positive_or_unknown_label_rejected():
    with pytest.raises(ValueError):
        metrics.sentence_kappa(
            [metrics.RowLabels("r", {"s1": "supported"}, {"s1": "supported"}, {})]
        )
    with pytest.raises(ValueError):
        metrics.confusion_matrix([row(["unknown"])])


def percentile(values, p):
    values = sorted(values)
    position = (len(values) - 1) * p
    lo = int(position)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (
        position - lo
    )


def test_cluster_bootstrap_seeded_three_values_and_row_draws():
    rows = [
        row(["supported"] * 8, key="a"),
        row(["unsupported"] * 8, key="b"),
        row(["supported", "unsupported"], ["unsupported", "supported"], key="c"),
    ]
    result = metrics.cluster_bootstrap_ci(rows, seed=19, resamples=500)
    assert result == metrics.cluster_bootstrap_ci(rows, seed=19, resamples=500)
    assert len(result) == 3
    rng = random.Random(19)
    draws = [metrics.sentence_kappa(rng.choices(rows, k=len(rows))) for _ in range(500)]
    valid = [value for value in draws if value is not None]
    assert result == pytest.approx(
        (percentile(valid, 0.025), percentile(valid, 0.975), draws.count(None))
    )
    # A sentence bootstrap with the same seed loses the within-row dependence.
    a = [0] * 8 + [1] * 8 + [0, 1]
    b = [0] * 8 + [1] * 8 + [1, 0]
    rng = random.Random(19)
    individual = []
    for _ in range(500):
        ix = rng.choices(range(len(a)), k=len(a))
        value = weighted_kappa(
            [a[i] for i in ix], [b[i] for i in ix], categories=(0, 1)
        )
        if value is not None:
            individual.append(value)
    assert result[:2] != (percentile(individual, 0.025), percentile(individual, 0.975))


def test_bootstrap_degenerate_defaults_and_invalid_resamples():
    assert metrics.cluster_bootstrap_ci([row(["supported"])]) is None
    assert metrics.cluster_bootstrap_ci([]) is None
    with pytest.raises(ValueError):
        metrics.cluster_bootstrap_ci([row(["supported", "unsupported"])], resamples=0)
    # Two homogeneous clusters: about half the samples are degenerate.
    result = metrics.cluster_bootstrap_ci(
        [row(["supported"], key="a"), row(["unsupported"], key="b")]
    )
    rng = random.Random(20261005)
    skipped = sum(len(set(rng.choices(range(2), k=2))) == 1 for _ in range(10000))
    assert result == (1.0, 1.0, skipped)


def test_confusion_precision_recall_and_row_kappa():
    rows = [
        row(
            [
                "unsupported",
                "contradicted",
                "unsupported",
                "supported",
                "not_claim",
                "supported",
            ],
            [
                "unsupported",
                "supported",
                "contradicted",
                "unsupported",
                "not_claim",
                "supported",
            ],
        ),
        row(["supported"], key="negative"),
    ]
    matrix = metrics.confusion_matrix(rows)
    assert len(matrix) == 4 and all(len(columns) == 4 for columns in matrix.values())
    assert sum(sum(columns.values()) for columns in matrix.values()) == 7
    assert matrix["unsupported"]["contradicted"] == 1
    result = metrics.precision_recall(rows)
    assert {k: result[k] for k in ("tp", "fp", "tn", "fn")} == {
        "tp": 2,
        "fp": 1,
        "tn": 3,
        "fn": 1,
    }
    assert result["precision"] == pytest.approx(2 / 3)
    assert result["recall"] == pytest.approx(2 / 3)
    assert metrics.row_level_kappa(rows) == 1


def test_detection_fpr_denominators_and_skips():
    injected = [
        (
            InjectedRow("a", "fact", (), "s2", None),
            RowAggregate({"s2": "unsupported"}, {"s2": True}, {}, True),
        ),
        (
            InjectedRow("b", "fabricated_id", (), "s1", "syn-test-001"),
            RowAggregate({}, {}, {}, False),
        ),
        InjectionSkip("c", "no_candidate"),
    ]
    clean = [
        row(
            ["supported", "supported", "not_claim"],
            ["unsupported", "supported", "contradicted"],
            key="a",
        ),
        row(["supported"], key="b"),
        row(["unsupported", "supported"], key="not-clean"),
    ]
    result = metrics.detection_and_fpr(injected, clean)
    assert result["detection_rate"] == 0.5
    assert result["false_positive_rate"] == pytest.approx(1 / 3)
    assert result["row_false_positive_rate"] == 0.5
    assert result["denominators"] == {
        "injected_rows": 2,
        "supported_sentences": 3,
        "clean_negative_rows": 2,
    }
    assert result["skip_count"] == 1
    assert result["skip_reasons"] == {"no_candidate": 1}
    assert (
        result["detected"]
        == result["false_positives"]
        == result["false_positive_rows"]
        == 1
    )


def test_zero_denominators_have_reasons():
    result = metrics.detection_and_fpr([], [])
    for key, reason in [
        ("detection_rate", "no_injected_rows"),
        ("false_positive_rate", "no_supported_sentences"),
        ("row_false_positive_rate", "no_clean_negative_rows"),
    ]:
        assert result[key] is None
        assert result["reasons"][key] == reason
    pr = metrics.precision_recall([row(["supported"])])
    assert pr["precision"] is pr["recall"] is None
    assert pr["reasons"] == {
        "precision": "no_model_positive_sentences",
        "recall": "no_human_positive_sentences",
    }
    only_format = metrics.detection_and_fpr([], [row(["not_claim"], ["unsupported"])])
    assert only_format["false_positive_rate"] is None
    assert only_format["row_false_positive_rate"] == 1


def validation_rows(positive_sentences=10, positive_rows=5):
    rows = []
    for i in range(positive_rows):
        n = positive_sentences // positive_rows + (
            i < positive_sentences % positive_rows
        )
        rows.append(row(["unsupported"] * n + ["supported"], key=str(i)))
    return rows


@pytest.mark.parametrize(
    "kappa,detection,fpr,n_pos,n_rows",
    itertools.product([0.599, 0.6], [0.899, 0.90], [0.10, 0.101], [9, 10], [4, 5]),
)
def test_all_32_verdict_boundary_combinations(
    monkeypatch, kappa, detection, fpr, n_pos, n_rows
):
    # Inject only the statistic to reach exact decimal boundaries; real kappa tested above.
    monkeypatch.setattr(metrics, "sentence_kappa", lambda rows: kappa)
    result = metrics.validation_verdict(
        rows=validation_rows(n_pos, n_rows),
        detection_rate=detection,
        false_positive_rate=fpr,
    )
    expected = (
        "판정 불가(양성 부족)"
        if n_pos < 10 or n_rows < 5
        else (
            "채점기 v3 검증 통과"
            if kappa >= 0.6 and detection >= 0.90 and fpr <= 0.10
            else "채점기 v3 검증 미달"
        )
    )
    assert result["verdict"] == expected
    assert result["criteria"]["kappa"]["met"] is (kappa >= 0.6)
    assert result["criteria"]["detection_rate"]["met"] is (detection >= 0.9)
    assert result["criteria"]["false_positive_rate"]["met"] is (fpr <= 0.1)


def test_validation_diagnostics_and_undefined_cases(monkeypatch):
    rows = validation_rows()
    result = metrics.validation_verdict(
        rows=rows, detection_rate=0.9, false_positive_rate=0.1
    )
    assert result["verdict"] == "채점기 v3 검증 통과"
    assert result["kappa"] == 1
    assert result["kappa_ci"] == (1, 1, 0)
    assert result["positive_sentences"] == 10 and result["negative_sentences"] == 5
    assert result["positive_rows"] == 5 and result["n_sentences"] == 15
    assert result["agreement"] == result["precision"] == result["recall"] == 1
    assert result["row_kappa"] is None
    assert result["denominators"]["sentences"] == 15
    assert sum(sum(col.values()) for col in result["confusion_matrix"].values()) == 15
    monkeypatch.setattr(metrics, "cluster_bootstrap_ci", lambda rows: None)
    assert (
        metrics.validation_verdict(
            rows=rows, detection_rate=1.0, false_positive_rate=0.0
        )["verdict"]
        == "판정 불가(κ 퇴화)"
    )


def test_undefined_point_estimate_insufficiency_precedence_and_no_vacuous_pass():
    rows = [row(["unsupported"] * 2, key=str(i)) for i in range(5)]
    assert (
        metrics.validation_verdict(
            rows=rows, detection_rate=1.0, false_positive_rate=0.0
        )["verdict"]
        == "판정 불가(κ 퇴화)"
    )
    assert (
        metrics.validation_verdict(
            rows=[], detection_rate=None, false_positive_rate=None
        )["verdict"]
        == "판정 불가(양성 부족)"
    )
    for detection, fpr in [(None, 0.0), (1.0, None), (None, None)]:
        assert (
            metrics.validation_verdict(
                rows=validation_rows(),
                detection_rate=detection,
                false_positive_rate=fpr,
            )["verdict"]
            == "채점기 v3 검증 미달"
        )


def test_thresholds_fixed_and_invalid_rates_rejected():
    assert (
        metrics.MIN_POS_SENTENCES,
        metrics.MIN_POS_ROWS,
        metrics.KAPPA_MIN,
        metrics.DETECTION_MIN,
        metrics.FPR_MAX,
    ) == (10, 5, 0.6, 0.9, 0.1)
    for rate in [-0.1, 1.1, float("nan"), float("inf")]:
        with pytest.raises(ValueError):
            metrics.validation_verdict(
                rows=validation_rows(), detection_rate=rate, false_positive_rate=0.0
            )
    with pytest.raises(TypeError):
        metrics.validation_verdict(
            rows=[], detection_rate=1.0, false_positive_rate=0.0, kappa_min=0.1
        )


def test_wilson_known_values_and_empty():
    low, high = metrics.wilson(19, 20)
    assert low == pytest.approx(0.7639, abs=1e-3) and high < 1
    low, high = metrics.wilson(20, 20)
    assert low == pytest.approx(0.8389, abs=1e-3) and high == pytest.approx(1)
    assert metrics.wilson(0, 0) is None and metrics.wilson(3, 2) is None
    assert metrics.wilson(0, 10)[0] == 0


def test_detection_by_kind_with_wilson_and_skip_causes():
    injected = [
        (
            InjectedRow(f"f{i}", "fact", (), "s2", None),
            RowAggregate({"s2": "unsupported"}, {"s2": True}, {}, True),
        )
        for i in range(3)
    ] + [
        (
            InjectedRow("w", "wrong_support", (), "s1", "syn-test-001"),
            RowAggregate({}, {}, {("s1", "syn-test-001"): True}, False),
        ),
        InjectionSkip("c", "no_candidate", "no_cited_sentence"),
        InjectionSkip("d", "no_candidate"),
    ]
    report = metrics.detection_and_fpr(injected, [row(["supported"])])
    assert (
        report["by_kind"]["fact"]["n"] == 3
        and report["by_kind"]["fact"]["detected"] == 3
    )
    assert report["by_kind"]["wrong_support"] == {
        "n": 1,
        "detected": 0,
        "rate": 0.0,
        "ci": metrics.wilson(0, 1),
    }
    assert report["detection_ci"] == metrics.wilson(3, 4)
    assert report["skip_causes"] == {"no_cited_sentence": 1}
    assert report["skip_reasons"] == {"no_candidate": 2}


def test_human_hallucination_rates_use_wilson_and_exclude_not_claim():
    labels = [
        {"sample_id": "S01", "label": "supported"},
        {"sample_id": "S01", "label": "unsupported"},
        {"sample_id": "S01", "label": "not_claim"},
        {"sample_id": "S02", "label": "supported"},
        {"sample_id": "S02", "label": "not_claim"},
    ]
    result = metrics.human_hallucination(labels)
    assert result["n_claim_sentences"] == 3 and result["n_positive_sentences"] == 1
    assert result["sentence_rate"] == pytest.approx(1 / 3)
    assert result["sentence_ci"] == metrics.wilson(1, 3)
    assert result["n_rows"] == 2 and result["row_rate"] == 0.5
    assert result["row_ci"] == metrics.wilson(1, 2)
    assert metrics.human_hallucination([])["sentence_rate"] is None
    with pytest.raises(ValueError):
        metrics.human_hallucination([{"sample_id": "S", "label": "bogus"}])


def limited(
    *,
    detected=20,
    injected=20,
    negatives=150,
    false_positives=0,
    ratio=0.0,
    extra_positive=None,
):
    labels = ["supported"] * negatives
    positives = {f"s{i}": i <= false_positives for i in range(1, negatives + 1)}
    rows = [row(labels, positives=positives)]
    if extra_positive:
        rows.append(row(["unsupported"] * extra_positive, key="p"))
    detection = {"detected": detected, "denominators": {"injected_rows": injected}}
    return metrics.validation_verdict(
        rows=rows,
        detection_rate=detected / injected if injected else None,
        false_positive_rate=false_positives / negatives,
        detection=detection,
        row_failure_ratio=ratio,
    )


def test_limited_validation_passes_only_when_every_condition_holds():
    result = limited()
    assert result["verdict"] == metrics.LIMITED_VERDICT
    assert result["original_verdict"] == "판정 불가(양성 부족)"
    assert result["limited_validation"]["met"] is True
    assert result["kappa"] is None  # the original statistics are still reported


@pytest.mark.parametrize(
    "kwargs,met",
    [
        ({"detected": 20, "injected": 20}, True),  # 20/20: Wilson lower 0.839
        ({"detected": 19, "injected": 20}, False),  # 19/20: lower 0.764 and < 20
        ({"detected": 19, "injected": 19}, False),  # lower 0.832 but only 19 detected
        ({"detected": 29, "injected": 30}, True),  # lower 0.833, 29 detected
        ({"detected": 20, "injected": 21}, False),  # 20/21 lower 0.78
        ({"detected": 0, "injected": 0}, False),
    ],
)
def test_limited_condition_i_detection_count_and_wilson_boundary(kwargs, met):
    result = limited(**kwargs)
    assert result["limited_validation"]["detected"]["met"] is met
    assert (result["verdict"] == metrics.LIMITED_VERDICT) is met


@pytest.mark.parametrize(
    "negatives,false_positives,met",
    [
        (150, 7, True),  # 0.0467
        (150, 8, False),  # 0.0533
        (149, 0, False),  # one short of 150 negative sentences
        (300, 15, True),  # exactly 0.05
        (300, 16, False),
    ],
)
def test_limited_condition_ii_negative_sentences_and_positive_rate(
    negatives, false_positives, met
):
    result = limited(negatives=negatives, false_positives=false_positives)
    assert result["limited_validation"]["negative_sentences"]["met"] is met
    assert (result["verdict"] == metrics.LIMITED_VERDICT) is met


@pytest.mark.parametrize(
    "ratio,met",
    [(0.0, True), (0.10, True), (0.1001, False), (0.15, False), (None, False)],
)
def test_limited_condition_iii_row_failure_ratio(ratio, met):
    result = limited(ratio=ratio)
    assert result["limited_validation"]["row_failure_ratio"]["met"] is met
    assert (result["verdict"] == metrics.LIMITED_VERDICT) is met
    assert result["original_verdict"] == "판정 불가(양성 부족)"


def test_limited_validation_is_not_applicable_when_the_original_verdict_is_decided():
    decided = limited(extra_positive=10)
    # 10 positive sentences in a single row: still too few positive rows.
    assert decided["original_verdict"] == "판정 불가(양성 부족)"
    rows = [row(["unsupported"] * 3 + ["supported"] * 3, key=f"p{i}") for i in range(5)]
    verdict = metrics.validation_verdict(
        rows=rows,
        detection_rate=1.0,
        false_positive_rate=0.0,
        detection={"detected": 20, "denominators": {"injected_rows": 20}},
        row_failure_ratio=0.0,
    )
    assert verdict["original_verdict"] == verdict["verdict"]
    assert verdict["verdict"] != metrics.LIMITED_VERDICT
    assert verdict["limited_validation"] is None
