"""Fixed preregistered v2 validity, independent of live classifier state."""

import pytest

from govops.eval.v2_validity import (
    MIN_OK_RATE,
    STATUS_KEYS,
    v2_validity,
    validity_label,
)


def test_fixed_contract():
    assert STATUS_KEYS == (
        "ok",
        "timeout",
        "rate_limited",
        "error",
        "parse",
        "v1_blocked",
    )
    assert MIN_OK_RATE == 0.95


@pytest.mark.parametrize("counts", [{}, {"ok": 1, "error": 99}])
def test_off_is_valid_even_with_attempts(counts):
    result = v2_validity("off", counts)
    assert result == {
        "valid": True,
        "reason": None,
        "attempts": sum(counts.values()),
        "ok": counts.get("ok", 0),
        "ok_rate": 0.01 if counts else None,
        "switch": "off",
    }


@pytest.mark.parametrize(
    "counts,valid,reason,attempts,ok,rate",
    [
        ({}, False, "no_attempts", 0, 0, None),
        ({"ok": 94, "timeout": 6}, False, "ok_rate_below_95", 100, 94, 0.94),
        ({"ok": 95, "parse": 5}, True, None, 100, 95, 0.95),
        ({"ok": 105, "v1_blocked": 1}, True, None, 106, 105, 105 / 106),
        ({"error": 1}, False, "ok_rate_below_95", 1, 0, 0.0),
        ({"ok": 1}, True, None, 1, 1, 1.0),
    ],
)
def test_on_validity(counts, valid, reason, attempts, ok, rate):
    assert v2_validity("on", counts) == {
        "valid": valid,
        "reason": reason,
        "attempts": attempts,
        "ok": ok,
        "ok_rate": rate,
        "switch": "on",
    }


def test_all_six_statuses_count_as_attempts():
    counts = dict.fromkeys(STATUS_KEYS, 1)
    result = v2_validity("on", counts)
    assert result["attempts"] == 6
    assert result["ok_rate"] == 1 / 6
    assert result["reason"] == "ok_rate_below_95"


@pytest.mark.parametrize("switch", ["on", "off"])
@pytest.mark.parametrize(
    "counts",
    [
        {"synthetic-sentinel": 1},
        {"ok": -1},
        {"ok": "synthetic-sentinel"},
        {"ok": 1.5},
        {"ok": True},
    ],
)
def test_invalid_counts_have_only_fixed_error(switch, counts):
    with pytest.raises(ValueError) as caught:
        v2_validity(switch, counts)
    assert str(caught.value) == "invalid_status_counts"
    assert "synthetic-sentinel" not in str(caught.value)


def test_invalid_switch_has_only_fixed_error():
    with pytest.raises(ValueError) as caught:
        v2_validity("synthetic-sentinel", {})
    assert str(caught.value) == "invalid_status_counts"


@pytest.mark.parametrize("valid,label", [(True, "valid"), (False, "invalid_v2_off")])
def test_validity_label_does_not_change_run_role(valid, label):
    validity = {"valid": valid, "run_role": "reference"}
    assert validity_label(validity) == label
    assert validity == {"valid": valid, "run_role": "reference"}
