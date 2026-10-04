"""multiprocessing 예약 경합 테스트용 작업 함수(spawn에서 import 가능해야 한다)."""

from pathlib import Path

from govops.costs import BudgetExceededError, CostLedger


def reserve_once(root: str, estimate: float) -> str:
    try:
        CostLedger(Path(root)).reserve(
            purpose="agent_primary",
            model="gpt-6-luna",
            task_id=None,
            estimate_usd=estimate,
        )
    except BudgetExceededError:
        return "rejected"
    return "reserved"
