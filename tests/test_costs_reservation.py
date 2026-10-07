"""예약·정산 원장: 상한 검사와 예약 기록이 잠금 안에서 한 번에 일어난다."""

import json
import multiprocessing
import threading

import pytest
from _cost_worker import reserve_once

from govops import costs
from govops.costs import BudgetExceededError, CostLedger, LedgerMissingError

CAP = costs.SUPPLIER_CAPS_USD["openai"]


def ledger(tmp_path, *, used: float = 0.0) -> CostLedger:
    costs.init_ledgers(tmp_path)
    result = CostLedger(tmp_path)
    if used:
        result.record(
            purpose="agent_primary",
            model="gpt-6-luna",
            task_id="old",
            input_tokens=0,
            output_tokens=0,
            cost_usd=used,
        )
    return result


def rows(tmp_path, name="costs.jsonl"):
    return [json.loads(x) for x in (tmp_path / name).read_text().splitlines()]


def reserve(target, estimate, purpose="agent_primary"):
    return target.reserve(
        purpose=purpose, model="gpt-6-luna", task_id="t", estimate_usd=estimate
    )


def settle(target, reservation_id, cost, purpose="agent_primary"):
    target.settle(
        reservation_id,
        purpose=purpose,
        model="gpt-6-luna",
        task_id="t",
        input_tokens=10 if cost else 0,
        output_tokens=5 if cost else 0,
        cost_usd=cost,
    )


def test_reserve_then_settle_replaces_estimate_with_actual(tmp_path):
    target = ledger(tmp_path)
    reservation = reserve(target, 0.5)
    assert target.committed_usd("openai") == pytest.approx(0.5)
    assert target.total_usd("openai") == 0
    settle(target, reservation, 0.1)
    assert target.committed_usd("openai") == pytest.approx(0.1)
    assert target.total_usd("openai") == pytest.approx(0.1)
    written = rows(tmp_path)
    assert [row.get("kind") for row in written] == ["reservation", None]
    assert written[0]["reservation_id"] == written[1]["reservation_id"] == reservation
    assert written[0]["estimate_usd"] == 0.5


def test_reserve_over_cap_is_rejected_and_writes_nothing(tmp_path):
    target = ledger(tmp_path, used=round(CAP - 0.01, 10))
    before = (tmp_path / "costs.jsonl").read_text()
    with pytest.raises(BudgetExceededError):
        reserve(target, 0.011)
    assert (tmp_path / "costs.jsonl").read_text() == before
    reserve(target, 0.01)  # 정확히 상한과 같으면 허용


def test_unsettled_reservation_stays_conservatively_counted(tmp_path):
    target = ledger(tmp_path, used=round(CAP - 0.01, 10))
    reserve(target, 0.008)  # 프로세스가 죽어 정산되지 않은 예약
    with pytest.raises(BudgetExceededError):
        reserve(target, 0.005)
    with pytest.raises(BudgetExceededError):
        target.check_before_call(purpose="agent_primary", estimate_usd=0.005)
    # 새 인스턴스(재시작)도 미정산 예약을 센다.
    assert CostLedger(tmp_path).committed_usd("openai") == pytest.approx(CAP - 0.002)
    assert len(rows(tmp_path)) == 2  # 예약 행이 삭제되지 않는다


def test_failed_call_settles_with_zero(tmp_path):
    target = ledger(tmp_path, used=round(CAP - 0.01, 10))
    settle(target, reserve(target, 0.01), 0)
    reserve(target, 0.01)


def test_settlement_may_exceed_estimate_and_counts_actual(tmp_path):
    target = ledger(tmp_path)
    settle(target, reserve(target, 0.1), 0.3)
    assert target.committed_usd("openai") == pytest.approx(0.3)


@pytest.mark.parametrize("case", ["unknown", "twice", "purpose"])
def test_invalid_settlement(tmp_path, case):
    target = ledger(tmp_path)
    reservation = reserve(target, 0.1)
    if case == "unknown":
        with pytest.raises(ValueError):
            settle(target, "nope", 0.1)
    elif case == "twice":
        settle(target, reservation, 0.1)
        with pytest.raises(ValueError):
            settle(target, reservation, 0.1)
    else:
        with pytest.raises(ValueError):
            settle(target, reservation, 0.1, purpose="grading")
    assert target.committed_usd("openai") >= 0


def test_missing_ledger_fails_closed(tmp_path):
    target = CostLedger(tmp_path)
    with pytest.raises(LedgerMissingError):
        reserve(target, 0.1)
    with pytest.raises(LedgerMissingError):
        settle(target, "x", 0.1)
    assert list(tmp_path.iterdir()) == []


def test_grading_reservations_use_the_grading_ledger(tmp_path):
    target = ledger(tmp_path)
    reservation = reserve(target, 0.2, purpose="grading")
    assert rows(tmp_path) == []
    assert rows(tmp_path, "costs_grading.jsonl")[0]["supplier"] == "gemini"
    assert target.committed_usd("gemini") == pytest.approx(0.2)
    assert target.committed_usd("openai") == 0
    settle(target, reservation, 0.05, purpose="grading")
    assert target.total_usd("gemini") == pytest.approx(0.05)


def test_ledger_rows_hold_no_prompt_or_identity(tmp_path):
    target = ledger(tmp_path)
    reserve(target, 0.1)
    assert set(rows(tmp_path)[0]) == {
        "ts",
        "kind",
        "reservation_id",
        "purpose",
        "supplier",
        "model",
        "task_id",
        "estimate_usd",
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.pop("estimate_usd"),
        lambda r: r.update(estimate_usd=-1),
        lambda r: r.update(supplier="anthropic"),
        lambda r: r.update(extra="x"),
    ],
)
def test_corrupt_reservation_row_is_an_error(tmp_path, mutate):
    target = ledger(tmp_path)
    reserve(target, 0.1)
    row = rows(tmp_path)[0]
    mutate(row)
    (tmp_path / "costs.jsonl").write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError):
        target.committed_usd("openai")


def test_old_rows_without_reservation_remain_valid(tmp_path):
    target = ledger(tmp_path, used=1.5)
    assert target.committed_usd("openai") == pytest.approx(1.5)


def test_threads_never_reserve_beyond_cap(tmp_path):
    target = ledger(tmp_path, used=round(CAP - 0.01, 10))
    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(16, timeout=30)

    def worker():
        barrier.wait()
        try:
            reserve(target, 0.004)
            outcome = "reserved"
        except BudgetExceededError:
            outcome = "rejected"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)
    assert results.count("reserved") == 2  # 0.01 / 0.004 = 2.5
    assert target.committed_usd("openai") <= CAP + 1e-9


def test_processes_never_reserve_beyond_cap(tmp_path):
    ledger(tmp_path, used=round(CAP - 0.01, 10))
    context = multiprocessing.get_context("spawn")
    pool = context.Pool(6)
    try:
        # 풀 워커가 죽으면 starmap은 영원히 끝나지 않는다(CI 정지 사례). 유한 대기로 바꾼다.
        outcomes = pool.starmap_async(reserve_once, [(str(tmp_path), 0.004)] * 12).get(
            timeout=60
        )
    finally:
        pool.terminate()
        pool.join()
    assert outcomes.count("reserved") == 2
    assert CostLedger(tmp_path).committed_usd("openai") <= CAP + 1e-9
    assert len([r for r in rows(tmp_path) if r.get("kind") == "reservation"]) == 2
