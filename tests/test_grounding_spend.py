"""Synthetic append-only M8 budget accounting and contention checks."""

import json
import stat
import threading

import pytest

from govops.grounding.spend import STAGE_CAPS_USD, M8Spend, SpendCapError


def test_reservation_settlement_and_restart(tmp_path):
    path = tmp_path / "nested/spend.jsonl"
    spend = M8Spend(path)
    assert spend.total_usd() == 0
    first = spend.reserve("calibration", 0.3)
    second = spend.reserve("validation", 0.2)
    before = path.read_bytes()
    assert M8Spend(path).total_usd() == pytest.approx(0.5)
    spend.settle(first, 0.1)
    assert path.read_bytes().startswith(before)
    assert spend.total_usd("calibration") == pytest.approx(0.1)
    assert spend.total_usd() == pytest.approx(0.3)
    spend.settle(second, 0.25)
    assert spend.total_usd() == pytest.approx(0.35)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["kind"] for row in rows] == ["reservation"] * 2 + ["settlement"] * 2
    assert all(
        set(row) == {"kind", "reservation_id", "stage", "amount_usd"} for row in rows
    )
    assert "prompt" not in path.read_text() and "task_id" not in path.read_text()


@pytest.mark.parametrize("stage,cap", STAGE_CAPS_USD.items())
def test_stage_boundary_and_check_is_read_only(tmp_path, stage, cap):
    spend = M8Spend(tmp_path / "spend.jsonl")
    reservation = spend.reserve(stage, cap)
    before = spend.path.read_bytes()
    spend.check(stage, 0)
    for operation in (spend.check, spend.reserve):
        with pytest.raises(SpendCapError):
            operation(stage, 0.000001)
    assert spend.path.read_bytes() == before
    spend.settle(reservation, 0)
    spend.reserve(stage, cap)


def test_total_cap_counts_actual_and_pending(tmp_path):
    spend = M8Spend(tmp_path / "spend.jsonl")
    reservation = spend.reserve("calibration", 0.8)
    spend.settle(reservation, 1.4)  # confirmed usage can exceed an estimate
    spend.reserve("validation", 0.6)
    assert spend.total_usd() == 2
    spend.check("measurement", 0)
    with pytest.raises(SpendCapError):
        spend.reserve("measurement", 0.000001)


@pytest.mark.parametrize("amount", [-1, float("nan"), float("inf"), True, "0.1"])
def test_invalid_amounts(tmp_path, amount):
    spend = M8Spend(tmp_path / "spend.jsonl")
    for operation in (spend.check, spend.reserve):
        with pytest.raises((TypeError, ValueError)):
            operation("calibration", amount)
    reservation = spend.reserve("calibration", 0)
    with pytest.raises((TypeError, ValueError)):
        spend.settle(reservation, amount)


def test_unknown_stage_and_reservations(tmp_path):
    spend = M8Spend(tmp_path / "spend.jsonl")
    with pytest.raises(ValueError):
        spend.reserve("unknown", 0)
    with pytest.raises(ValueError):
        spend.total_usd("unknown")
    with pytest.raises(ValueError):
        spend.settle("unknown", 0)
    reservation = spend.reserve("validation", 0.1)
    spend.settle(reservation, 0.05)
    before = spend.path.read_bytes()
    with pytest.raises(ValueError):
        spend.settle(reservation, 0.05)
    assert spend.path.read_bytes() == before


@pytest.mark.parametrize("corruption", ["{", "{}\n", "\n", '{"kind":"reservation"}\n'])
def test_corrupt_rows_fail_closed(tmp_path, corruption):
    path = tmp_path / "spend.jsonl"
    path.write_text(corruption)
    spend = M8Spend(path)
    for operation in (
        lambda: spend.total_usd(),
        lambda: spend.reserve("validation", 0),
    ):
        with pytest.raises(ValueError, match="invalid M8 spend"):
            operation()
    assert path.read_text() == corruption


def test_duplicate_or_orphan_rows_are_corrupt(tmp_path):
    path = tmp_path / "spend.jsonl"
    spend = M8Spend(path)
    reservation = spend.reserve("calibration", 0.1)
    reserved = path.read_text()
    path.write_text(reserved + reserved)
    with pytest.raises(ValueError):
        spend.total_usd()
    path.write_text(reserved)
    spend.settle(reservation, 0.05)
    settlement = path.read_text().splitlines()[-1] + "\n"
    path.write_text(settlement)
    with pytest.raises(ValueError):
        spend.total_usd()


def test_sixteen_threads_cannot_overreserve(tmp_path):
    path = tmp_path / "spend.jsonl"
    barrier = threading.Barrier(16, timeout=30)
    outcomes = []
    lock = threading.Lock()

    def worker():
        try:
            barrier.wait()
            M8Spend(path).reserve("calibration", 0.3)
            outcome = "reserved"
        except SpendCapError:
            outcome = "rejected"
        except Exception as exc:  # noqa: BLE001 — surface worker failures
            outcome = type(exc).__name__
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["rejected"] * 14 + ["reserved"] * 2
    assert M8Spend(path).total_usd() == pytest.approx(0.6)
