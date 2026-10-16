"""Pure M8 validation statistics and preregistered, non-configurable verdicts."""

import math
import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from govops.eval.kappa import weighted_kappa
from govops.grounding.grader import LABELS, POSITIVE_LABELS, RowAggregate
from govops.grounding.inject import InjectedRow, InjectionSkip, injection_detected

MIN_POS_SENTENCES = 10
MIN_POS_ROWS = 5
KAPPA_MIN = 0.6
DETECTION_MIN = 0.90
FPR_MAX = 0.10
# Addendum 1 §D ("제한적 검증"): applies only when the original verdict is
# "판정 불가(양성 부족)". Fixed constants; callers cannot change them.
LIMITED_MIN_DETECTED = 20
LIMITED_MIN_WILSON_LOWER = 0.80
LIMITED_MIN_NEGATIVE_SENTENCES = 150
LIMITED_MAX_NEGATIVE_POSITIVE_RATE = 0.05
LIMITED_MAX_ROW_FAILURE_RATIO = 0.10
LIMITED_VERDICT = "제한적 검증(자연 양성 부족)"
Z95 = 1.959963984540054


@dataclass(frozen=True)
class RowLabels:
    row_key: str
    human: dict[str, str]
    model: dict[str, str]
    model_positive: dict[str, bool]


def _validate(rows: Sequence[RowLabels]) -> None:
    for row in rows:
        if set(row.human) != set(row.model) or set(row.human) != set(
            row.model_positive
        ):
            raise ValueError("sentence ID sets differ")
        if any(
            label not in LABELS for label in [*row.human.values(), *row.model.values()]
        ):
            raise ValueError("unknown sentence label")
        if any(type(value) is not bool for value in row.model_positive.values()):
            raise ValueError("invalid model positive value")


def _binary(row: RowLabels) -> tuple[list[int], list[int]]:
    ids = sorted(row.human)
    # The registered 2/3 positive vote can differ from the four-label plurality.
    return (
        [int(row.human[sid] in POSITIVE_LABELS) for sid in ids],
        [int(row.model_positive[sid]) for sid in ids],
    )


def sentence_kappa(rows: Sequence[RowLabels]) -> float | None:
    """Pool all sentences; binary model labels use the aggregate majority vote."""
    _validate(rows)
    chunks = [_binary(row) for row in rows]
    return weighted_kappa(
        [v for a, _ in chunks for v in a],
        [v for _, b in chunks for v in b],
        categories=(0, 1),
    )


def _percentile(values: Sequence[float], p: float) -> float:
    position = (len(values) - 1) * p
    lo, hi = math.floor(position), math.ceil(position)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def cluster_bootstrap_ci(
    rows: Sequence[RowLabels], *, seed=20261005, resamples=10000
) -> tuple[float, float, int] | None:
    """95% percentile interval from whole-row draws; omit undefined draws."""
    _validate(rows)
    if type(resamples) is not int or resamples <= 0:
        raise ValueError("invalid bootstrap resamples")
    if not rows:
        return None
    chunks = [_binary(row) for row in rows]
    rng = random.Random(seed)
    values = []
    skipped = 0
    for _ in range(resamples):
        sample = rng.choices(chunks, k=len(chunks))
        value = weighted_kappa(
            [v for a, _ in sample for v in a],
            [v for _, b in sample for v in b],
            categories=(0, 1),
        )
        if value is None:
            skipped += 1
        else:
            values.append(value)
    if not values:
        return None
    values.sort()
    return _percentile(values, 0.025), _percentile(values, 0.975), skipped


def confusion_matrix(rows: Sequence[RowLabels]) -> dict[str, dict[str, int]]:
    _validate(rows)
    matrix = {human: dict.fromkeys(LABELS, 0) for human in LABELS}
    for row in rows:
        for sid, label in row.human.items():
            matrix[label][row.model[sid]] += 1
    return matrix


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    """Wilson score interval; None without observations."""
    if n <= 0 or not 0 <= successes <= n:
        return None
    p = successes / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def human_hallucination(label_rows: Sequence[dict]) -> dict:
    """Human-label hallucination rates (independent of the grader), Wilson 95% CIs.

    ``label_rows`` hold ``sample_id`` and ``label`` per sentence.
    """
    positives = claims = 0
    rows: dict[str, bool] = {}
    for row in label_rows:
        label = row["label"]
        if label not in LABELS:
            raise ValueError("unknown sentence label")
        rows.setdefault(row["sample_id"], False)
        if label != "not_claim":
            claims += 1
        if label in POSITIVE_LABELS:
            positives += 1
            rows[row["sample_id"]] = True
    positive_rows = sum(rows.values())
    return {
        "n_rows": len(rows),
        "n_sentences": len(label_rows),
        "n_claim_sentences": claims,
        "n_positive_sentences": positives,
        "sentence_rate": _ratio(positives, claims),
        "sentence_ci": wilson(positives, claims),
        "n_positive_rows": positive_rows,
        "row_rate": _ratio(positive_rows, len(rows)),
        "row_ci": wilson(positive_rows, len(rows)),
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def precision_recall(rows: Sequence[RowLabels]) -> dict:
    _validate(rows)
    counts = dict.fromkeys(("tp", "fp", "tn", "fn"), 0)
    for row in rows:
        for human, model in zip(*_binary(row), strict=True):
            counts[
                ("tp" if model else "fn") if human else ("fp" if model else "tn")
            ] += 1
    predicted = counts["tp"] + counts["fp"]
    actual = counts["tp"] + counts["fn"]
    reasons = {}
    if not predicted:
        reasons["precision"] = "no_model_positive_sentences"
    if not actual:
        reasons["recall"] = "no_human_positive_sentences"
    return {
        **counts,
        "precision": _ratio(counts["tp"], predicted),
        "recall": _ratio(counts["tp"], actual),
        "denominators": {"precision": predicted, "recall": actual},
        "reasons": reasons,
    }


def row_level_kappa(rows: Sequence[RowLabels]) -> float | None:
    _validate(rows)
    return weighted_kappa(
        [
            int(any(label in POSITIVE_LABELS for label in row.human.values()))
            for row in rows
        ],
        [int(any(row.model_positive.values())) for row in rows],
        categories=(0, 1),
    )


def detection_and_fpr(
    injected: Sequence[tuple[InjectedRow, RowAggregate] | InjectionSkip],
    clean: Sequence[RowLabels],
) -> dict:
    """Keep skips separate; FPR uses only human-negative, unmodified rows."""
    _validate(clean)
    skip_reasons = Counter()
    skip_causes = Counter()
    detected = injection_count = 0
    by_kind: dict[str, list[int]] = {}
    for entry in injected:
        if isinstance(entry, InjectionSkip):
            skip_reasons[entry.reason] += 1
            if entry.cause:
                skip_causes[entry.cause] += 1
        else:
            injection_count += 1
            hit = injection_detected(*entry)
            detected += hit
            counts = by_kind.setdefault(entry[0].kind, [0, 0])
            counts[0] += 1
            counts[1] += hit
    normal = [
        row
        for row in clean
        if not any(label in POSITIVE_LABELS for label in row.human.values())
    ]
    supported = false_positives = false_positive_rows = 0
    for row in normal:
        for sid, label in row.human.items():
            if label == "supported":
                supported += 1
                false_positives += row.model_positive[sid]
        false_positive_rows += any(row.model_positive.values())
    denominators = {
        "injected_rows": injection_count,
        "supported_sentences": supported,
        "clean_negative_rows": len(normal),
    }
    reasons = {}
    for name, denominator, reason in [
        ("detection_rate", injection_count, "no_injected_rows"),
        ("false_positive_rate", supported, "no_supported_sentences"),
        ("row_false_positive_rate", len(normal), "no_clean_negative_rows"),
    ]:
        if not denominator:
            reasons[name] = reason
    return {
        "detection_rate": _ratio(detected, injection_count),
        "false_positive_rate": _ratio(false_positives, supported),
        "row_false_positive_rate": _ratio(false_positive_rows, len(normal)),
        "detected": detected,
        "false_positives": false_positives,
        "false_positive_rows": false_positive_rows,
        "denominators": denominators,
        "detection_ci": wilson(detected, injection_count),
        "by_kind": {
            kind: {
                "n": n,
                "detected": hit,
                "rate": _ratio(hit, n),
                "ci": wilson(hit, n),
            }
            for kind, (n, hit) in sorted(by_kind.items())
        },
        "skip_count": sum(skip_reasons.values()),
        "skip_reasons": dict(skip_reasons),
        "skip_causes": dict(skip_causes),
        "reasons": reasons,
    }


def validation_verdict(
    *,
    rows: Sequence[RowLabels],
    detection_rate: float | None,
    false_positive_rate: float | None,
    detection: dict | None = None,
    row_failure_ratio: float | None = None,
) -> dict:
    """Apply §10, retaining diagnostics even for insufficient/degenerate samples.

    Injection-rate counts live in detection_and_fpr's report: scalar rate arguments
    alone cannot recover those denominators. This report includes all denominators
    calculable from the supplied validation rows.
    """
    for rate in (detection_rate, false_positive_rate):
        if rate is not None and (not math.isfinite(rate) or not 0 <= rate <= 1):
            raise ValueError("invalid validation rate")
    kappa = sentence_kappa(rows)
    ci = cluster_bootstrap_ci(rows)
    pr = precision_recall(rows)
    positive = pr["tp"] + pr["fn"]
    negative = pr["tn"] + pr["fp"]
    n = positive + negative
    positive_rows = sum(
        any(label in POSITIVE_LABELS for label in row.human.values()) for row in rows
    )
    criteria = {
        "positive_sentences": {
            "value": positive,
            "threshold": MIN_POS_SENTENCES,
            "met": positive >= MIN_POS_SENTENCES,
        },
        "positive_rows": {
            "value": positive_rows,
            "threshold": MIN_POS_ROWS,
            "met": positive_rows >= MIN_POS_ROWS,
        },
        "kappa": {
            "value": kappa,
            "threshold": KAPPA_MIN,
            "met": kappa is not None and kappa >= KAPPA_MIN,
        },
        "detection_rate": {
            "value": detection_rate,
            "threshold": DETECTION_MIN,
            "met": detection_rate is not None and detection_rate >= DETECTION_MIN,
        },
        "false_positive_rate": {
            "value": false_positive_rate,
            "threshold": FPR_MAX,
            "met": false_positive_rate is not None and false_positive_rate <= FPR_MAX,
        },
    }
    if positive < MIN_POS_SENTENCES or positive_rows < MIN_POS_ROWS:
        verdict = "판정 불가(양성 부족)"
    elif kappa is None or ci is None:
        verdict = "판정 불가(κ 퇴화)"
    elif (
        criteria["kappa"]["met"]
        and criteria["detection_rate"]["met"]
        and criteria["false_positive_rate"]["met"]
    ):
        verdict = "채점기 v3 검증 통과"
    else:
        verdict = "채점기 v3 검증 미달"
    original = verdict
    limited = None
    if original == "판정 불가(양성 부족)":
        det = detection or {}
        detected = det.get("detected")
        injected_n = (det.get("denominators") or {}).get("injected_rows") or 0
        interval = wilson(detected, injected_n) if detected is not None else None
        negatives = pr["tn"] + pr["fp"]
        negative_rate = pr["fp"] / negatives if negatives else None
        limited = {
            "applicable": True,
            "detected": {
                "value": detected,
                "n": injected_n,
                "wilson_lower": interval[0] if interval else None,
                "threshold": LIMITED_MIN_DETECTED,
                "wilson_threshold": LIMITED_MIN_WILSON_LOWER,
                "met": detected is not None
                and interval is not None
                and detected >= LIMITED_MIN_DETECTED
                and interval[0] >= LIMITED_MIN_WILSON_LOWER,
            },
            "negative_sentences": {
                "value": negatives,
                "positive_rate": negative_rate,
                "threshold": LIMITED_MIN_NEGATIVE_SENTENCES,
                "rate_threshold": LIMITED_MAX_NEGATIVE_POSITIVE_RATE,
                "met": negatives >= LIMITED_MIN_NEGATIVE_SENTENCES
                and negative_rate is not None
                and negative_rate <= LIMITED_MAX_NEGATIVE_POSITIVE_RATE,
            },
            "row_failure_ratio": {
                "value": row_failure_ratio,
                "threshold": LIMITED_MAX_ROW_FAILURE_RATIO,
                "met": row_failure_ratio is not None
                and row_failure_ratio <= LIMITED_MAX_ROW_FAILURE_RATIO,
            },
        }
        limited["met"] = all(
            limited[name]["met"]
            for name in ("detected", "negative_sentences", "row_failure_ratio")
        )
        if limited["met"]:
            verdict = LIMITED_VERDICT
    return {
        **pr,
        "verdict": verdict,
        "original_verdict": original,
        "limited_validation": limited,
        "kappa": kappa,
        "kappa_ci": ci,
        "confusion_matrix": confusion_matrix(rows),
        "agreement": _ratio(pr["tp"] + pr["tn"], n),
        "n_sentences": n,
        "n_rows": len(rows),
        "positive_sentences": positive,
        "negative_sentences": negative,
        "positive_rows": positive_rows,
        "row_kappa": row_level_kappa(rows),
        "detection_rate": detection_rate,
        "false_positive_rate": false_positive_rate,
        "criteria": criteria,
        "denominators": {**pr["denominators"], "sentences": n, "rows": len(rows)},
    }
