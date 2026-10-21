"""Registered rollout policy, using synthetic conversations only."""

import hashlib
import random
import re
from pathlib import Path

import pytest

from govops.rollout import policy as p


def outcomes(n=20, *, abnormal=0, blocked=0, duration=100.0, status="ok"):
    return [
        p.ConversationOutcome(
            f"{i:032x}",
            i < blocked,
            {
                f"{i:032x}": [
                    p.ClassifyCall("timeout" if i < abnormal else status, 0, duration)
                ]
            },
        )
        for i in range(n)
    ]


def test_bucket_and_digest_are_stable_and_distribution_is_registered():
    rng = random.Random(20261021)
    labels = [f"synthetic-{rng.getrandbits(128):032x}" for _ in range(10000)]
    state = p.RolloutState(1000, False, None, None)
    for label in labels:
        digest = hashlib.sha256(("govops-m8-rollout-v1" + label).encode()).hexdigest()
        assert p.user_digest(label) == digest
        assert p.bucket(label) == int(digest[:8], 16) % 10000
        assert p.bucket(label) == p.bucket(label)
        assert 0 <= p.bucket(label) < 10000
    assert (
        0.09 <= sum(p.assign(label, state) == "v2" for label in labels) / 10000 <= 0.11
    )
    for pct, kill, variant in [
        (0, False, "v1"),
        (10000, False, "v2"),
        (10000, True, "v1"),
    ]:
        assert {
            p.assign(label, p.RolloutState(pct, kill, None, None)) for label in labels
        } == {variant}


@pytest.mark.parametrize("pct", [-1, 10001, True, False, 1.0, "1000", None])
@pytest.mark.parametrize("kill", [False, True])
def test_invalid_percentage_is_rejected(pct, kill):
    with pytest.raises(ValueError):
        p.assign("synthetic", p.RolloutState(pct, kill, None, None))


def test_assignment_uses_strict_bucket_boundary():
    value = p.bucket("synthetic")
    assert p.assign("synthetic", p.RolloutState(value, False, None, None)) == "v1"
    assert p.assign("synthetic", p.RolloutState(value + 1, False, None, None)) == "v2"


@pytest.mark.parametrize(
    "intervals,expected",
    [
        ([], 0.0),
        ([(0, 10), (5, 15)], 15.0),
        ([(20, 25), (0, 10)], 15.0),
        ([(0, 20), (5, 10)], 20.0),
        ([(0, 10), (10, 20), (20, 20)], 20.0),
    ],
)
def test_union_ms(intervals, expected):
    assert p.union_ms([p.ClassifyCall("ok", a, b) for a, b in intervals]) == expected


@pytest.mark.parametrize(
    "values,expected",
    [([], None), ([7], 7), (list(range(1, 21)), 19), (list(range(1, 22)), 20)],
)
def test_nearest_rank_p95(values, expected):
    assert p.nearest_rank_p95(values[::-1]) == expected


@pytest.mark.parametrize(
    "abnormal,reasons", [(1, ()), (2, ("rollback_classify_abnormal",))]
)
def test_abnormal_threshold(abnormal, reasons):
    result = p.evaluate(outcomes(abnormal=abnormal), outcomes())
    assert result.abnormal_rate == abnormal / 20
    assert result.reasons == reasons
    assert result.action == ("rollback" if reasons else "none")


@pytest.mark.parametrize(
    "v2_blocked,v1_blocked,rollback", [(2, 0, False), (8, 6, False), (3, 0, True)]
)
def test_block_difference_threshold(v2_blocked, v1_blocked, rollback):
    result = p.evaluate(outcomes(blocked=v2_blocked), outcomes(blocked=v1_blocked))
    assert result.block_diff == pytest.approx((v2_blocked - v1_blocked) / 20)
    assert result.reasons == (("rollback_block_diff",) if rollback else ())


@pytest.mark.parametrize(
    "duration,reasons", [(8000.0, ()), (8000.01, ("rollback_classify_p95",))]
)
def test_latency_threshold(duration, reasons):
    result = p.evaluate(outcomes(duration=duration), outcomes())
    assert result.p95_ms == duration
    assert result.reasons == reasons


def test_p95_is_per_turn_union_not_sum_or_conversation_total():
    rows = outcomes(duration=8000)
    rows[0] = p.ConversationOutcome(
        "0" * 32,
        False,
        {
            "a": [p.ClassifyCall("ok", 0, 5000), p.ClassifyCall("ok", 3000, 8000)],
            "b": [p.ClassifyCall("ok", 0, 8000)],
        },
    )
    result = p.evaluate(rows, outcomes())
    assert result.p95_ms == 8000
    assert result.action == "none"


@pytest.mark.parametrize("count", [0, 19])
def test_insufficient_v2_sample(count):
    assert p.evaluate(outcomes(count), outcomes()) == p.Evaluation(
        "none", (), ("insufficient_v2_sample",), None, None, None
    )


def test_insufficient_v1_only_defers_block_difference():
    result = p.evaluate(outcomes(abnormal=2, blocked=20, duration=8001), outcomes(19))
    assert result.notes == ("insufficient_v1_control",)
    assert result.block_diff is None
    assert result.reasons == ("rollback_classify_abnormal", "rollback_classify_p95")


@pytest.mark.parametrize("turns", [{}, {"empty": []}])
def test_no_classification_calls_rolls_back(turns):
    rows = [p.ConversationOutcome(f"{i:032x}", True, turns) for i in range(20)]
    result = p.evaluate(rows, outcomes())
    assert result.reasons == ("rollback_no_classify_calls", "rollback_block_diff")
    assert result.action == "rollback"
    assert result.abnormal_rate is None
    assert result.p95_ms is None


def test_v1_blocked_is_only_in_denominator():
    result = p.evaluate(outcomes(abnormal=1, status="v1_blocked"), outcomes())
    assert result.abnormal_rate == 0.05
    assert result.action == "none"


def test_multiple_reasons_follow_registration_order():
    result = p.evaluate(outcomes(abnormal=2, blocked=3, duration=8001), outcomes())
    assert result.reasons == p.ROLLBACK_CODES[1:]


def test_only_first_window_is_evaluated():
    result = p.evaluate(
        outcomes() + outcomes(abnormal=20, status="undefined"),
        outcomes() + outcomes(blocked=20),
    )
    assert result == p.Evaluation("none", (), (), 0.0, 0.0, 100.0)


def test_unknown_status_is_rejected():
    with pytest.raises(ValueError):
        p.evaluate(outcomes(status="undefined"), outcomes())


def test_constants_and_codes_match_frozen_registration():
    doc = (
        Path(__file__).resolve().parents[1] / "docs/results/hypotheses-m8-rollout.md"
    ).read_text()
    codes = doc.split("## 14.")[1].split("## 15.")[0]
    evaluation_row = next(
        line for line in codes.splitlines() if line.startswith("| 평가 |")
    )
    registered = re.findall(r"`([^`]+)`", evaluation_row)
    assert p.ROLLBACK_CODES == tuple(registered[2:])
    status_row = next(
        line for line in codes.splitlines() if line.startswith("| 분류 기록 상태 |")
    )
    assert p.CLASSIFY_STATUSES == frozenset(re.findall(r"`([^`]+)`", status_row))
    abnormal = re.search(r"status ∈ \{([^}]+)\}", doc).group(1)
    assert p.ABNORMAL_STATUSES == frozenset(abnormal.split(", "))
    assert p.SALT == re.search(r'SALT = "([^"]+)"', doc).group(1)
    assert p.WINDOW == int(re.search(r"앞 (\d+)개\(K=", doc).group(1))
    assert p.MIN_SAMPLE == int(re.search(r"최소 표본 (\d+)", doc).group(1))
    assert p.CANDIDATE_LIMIT == int(re.search(r"limit=(\d+)", doc).group(1))
    assert p.CLASSIFY_TAIL == int(re.search(r"마지막 (\d+)줄", doc).group(1))
    assert p.ABNORMAL_RATE_MAX == float(
        re.search(r"비정상 비율 > ([\d.]+)", doc).group(1)
    )
    assert p.BLOCK_DIFF_MAX == float(re.search(r"차 > ([\d.]+)", doc).group(1))
    assert p.P95_MS_MAX == float(re.search(r"p95 > (\d+) ms", doc).group(1))


def test_records_invalid_code_matches_addendum_registration():
    doc = (
        Path(__file__).resolve().parents[1]
        / "docs/results/hypotheses-m8-rollout-addendum-1.md"
    ).read_text()
    table = doc.split("## 2.")[1].split("## 3.")[0]
    rows = [line for line in table.splitlines() if line.startswith("| 평가 |")]
    assert len(rows) == 1
    registered = re.findall(r"`([^`]+)`", rows[0])
    assert registered == [p.RECORDS_INVALID_CODE]
    assert p.RECORDS_INVALID_CODE not in p.ROLLBACK_CODES
    assert f'policy.RECORDS_INVALID_CODE = "{p.RECORDS_INVALID_CODE}"' in doc
