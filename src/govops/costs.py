"""공급사별 append-only 비용 원장과 전체 기간 호출 직전 상한 검사."""

import argparse
import fcntl
import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

SUPPLIER_CAPS_USD: Mapping[str, float] = MappingProxyType(
    {"openai": 9.13, "anthropic": 11.47, "gemini": 7.29}
)
PURPOSES = ("agent_primary", "agent_fallback", "guardrail_v2", "grading")
PURPOSE_SUPPLIER: Mapping[str, str] = MappingProxyType(
    dict(zip(PURPOSES, ("openai", "anthropic", "anthropic", "gemini"), strict=True))
)


@dataclass(frozen=True)
class Price:
    service: str
    unit: str
    unit_price_usd: float
    source_url: str
    checked_on: str
    billing_margin: float = 1.0
    valid_until: str | None = None


# A costs.py 기준, 재확인 필요 (M2 파일럿 전).
PRICES: Mapping[str, Price] = MappingProxyType(
    {
        f"openai.gpt-6-luna.{suffix}": Price(
            service=f"openai.gpt-6-luna.{suffix}",
            unit="token",
            unit_price_usd=amount / 1_000_000,
            source_url="https://developers.openai.com/api/docs/pricing",
            checked_on="2026-09-30",
        )
        for suffix, amount in (
            ("input", 0.10),
            ("cached_input", 0.01),
            ("cache_write", 0.125),
            ("output", 0.50),
        )
    }
)


class LedgerMissingError(RuntimeError):
    """원장 소실 시 검사와 기록을 거부한다."""


class BudgetExceededError(RuntimeError):
    """추정 호출 비용이 공급사 전체 기간 상한을 초과한다."""


def _amount(value: float) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("cost must be a non-negative finite number")
    if not math.isfinite(value) or value < 0:
        raise ValueError("cost must be a non-negative finite number")
    return Decimal(str(value))


def _tokens(*values: int) -> None:
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("token counts must be non-negative integers")


def token_cost_usd(
    model: str,
    *,
    input_tokens: int,
    cached_input_tokens: int = 0,
    output_tokens: int,
) -> float:
    """input_tokens는 캐시 입력을 포함한 전체 입력 토큰 수다."""
    _tokens(input_tokens, cached_input_tokens, output_tokens)
    if cached_input_tokens > input_tokens:
        raise ValueError("cached input exceeds total input")
    return (
        (input_tokens - cached_input_tokens)
        * PRICES[f"openai.{model}.input"].unit_price_usd
        + cached_input_tokens * PRICES[f"openai.{model}.cached_input"].unit_price_usd
        + output_tokens * PRICES[f"openai.{model}.output"].unit_price_usd
    )


def estimate_call_usd(
    model: str,
    *,
    input_tokens_upper: int,
    max_output_tokens: int,
) -> float:
    return token_cost_usd(
        model, input_tokens=input_tokens_upper, output_tokens=max_output_tokens
    )


def _default_root() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "manifest"


_FILENAMES = ("costs.jsonl", "costs_grading.jsonl")
_FIELDS = {
    "ts",
    "purpose",
    "supplier",
    "model",
    "task_id",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "cost_usd",
}


def _validate(row: object, *, grading: bool) -> None:
    if not isinstance(row, dict) or set(row) != _FIELDS:
        raise ValueError("invalid cost ledger fields")
    purpose = row["purpose"]
    if not isinstance(purpose, str) or purpose not in PURPOSE_SUPPLIER:
        raise ValueError("invalid cost ledger purpose")
    if (purpose == "grading") != grading or row["supplier"] != PURPOSE_SUPPLIER[
        purpose
    ]:
        raise ValueError("invalid cost ledger routing")
    _tokens(row["input_tokens"], row["cached_input_tokens"], row["output_tokens"])
    if row["cached_input_tokens"] > row["input_tokens"]:
        raise ValueError("cached input exceeds total input")
    _amount(row["cost_usd"])
    if not isinstance(row["model"], str) or not row["model"]:
        raise ValueError("invalid model")
    if row["task_id"] is not None and not isinstance(row["task_id"], str):
        raise ValueError("invalid task_id")
    if not isinstance(row["ts"], str):
        raise TypeError("invalid timestamp")
    timestamp = datetime.fromisoformat(row["ts"])
    if timestamp.utcoffset() != UTC.utcoffset(None):
        raise ValueError("timestamp must be UTC")


class CostLedger:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else _default_root()

    def _open(self, path: Path, mode: str):
        try:
            return path.open(mode, encoding="utf-8")
        except FileNotFoundError:
            raise LedgerMissingError(f"Cost ledger missing: {path}") from None

    def _require_ledgers(self) -> None:
        for filename in _FILENAMES:
            with self._open(self.root / filename, "r"):
                pass

    def record(
        self,
        *,
        purpose: str,
        model: str,
        task_id: str | None,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        cost_usd: float,
    ) -> None:
        self._require_ledgers()
        row = {
            "ts": datetime.now(UTC).isoformat(),
            "purpose": purpose,
            "supplier": PURPOSE_SUPPLIER[purpose],
            "model": model,
            "task_id": task_id,
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
        }
        grading = purpose == "grading"
        _validate(row, grading=grading)
        line = json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
        # r+ never creates a missing ledger; seek and append under exclusive lock.
        with self._open(self.root / _FILENAMES[grading], "r+") as target:
            fcntl.flock(target, fcntl.LOCK_EX)
            target.seek(0, os.SEEK_END)
            target.write(line)
            target.flush()
            os.fsync(target.fileno())

    def _total(self, supplier: str) -> Decimal:
        if supplier not in SUPPLIER_CAPS_USD:
            raise ValueError("unknown supplier")
        self._require_ledgers()
        grading = supplier == "gemini"
        total = Decimal(0)
        with self._open(self.root / _FILENAMES[grading], "r") as source:
            fcntl.flock(source, fcntl.LOCK_SH)
            for number, line in enumerate(source, 1):
                try:
                    row = json.loads(line)
                    _validate(row, grading=grading)
                except (ValueError, TypeError, KeyError):
                    raise ValueError(f"Invalid cost ledger line {number}") from None
                if row["supplier"] == supplier:
                    total += _amount(row["cost_usd"])
        return total

    def total_usd(self, supplier: str) -> float:
        return float(self._total(supplier))

    def check_before_call(self, *, purpose: str, estimate_usd: float) -> None:
        supplier = PURPOSE_SUPPLIER[purpose]
        estimate = _amount(estimate_usd)
        total = self._total(supplier)
        cap = Decimal(str(SUPPLIER_CAPS_USD[supplier]))
        if total + estimate > cap:
            raise BudgetExceededError(
                f"{supplier}: total={total}, estimate={estimate}, cap={cap}"
            )


def init_ledgers(root: Path | None = None) -> list[Path]:
    root = Path(root) if root is not None else _default_root()
    root.mkdir(parents=True, exist_ok=True)
    created = []
    for filename in _FILENAMES:
        path = root / filename
        try:
            with path.open("x", encoding="utf-8") as target:
                target.flush()
                os.fsync(target.fileno())
        except FileExistsError:
            continue
        created.append(path)
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init"])
    parser.parse_args(argv)
    root = _default_root()
    created = init_ledgers(root)
    for filename in _FILENAMES:
        path = root / filename
        print(f"{path}" if path in created else f"이미 있음: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
