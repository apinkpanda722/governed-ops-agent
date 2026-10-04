"""공급사별 append-only 비용 원장과 전체 기간 호출 직전 상한 검사."""

import argparse
import fcntl
import json
import math
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from zoneinfo import ZoneInfo

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
        **{
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
        },
        **{
            f"google.gemini-3.8-flash.{suffix}": Price(
                service=f"google.gemini-3.8-flash.{suffix}",
                unit="token",
                unit_price_usd=amount / 1_000_000,
                source_url="https://ai.google.dev/gemini-api/docs/pricing",
                checked_on="2026-10-01",
                valid_until="2026-12-31",
            )
            for suffix, amount in (("input", 0.75), ("output", 3.75))
        },
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


class PriceExpiredError(RuntimeError):
    """The registered temporary price has expired."""


def ensure_price_valid(service: str, *, on: date | None = None) -> None:
    price = PRICES[service]
    today = on if on is not None else datetime.now(ZoneInfo("Asia/Seoul")).date()
    if price.valid_until and today > date.fromisoformat(price.valid_until):
        raise PriceExpiredError(f"Price expired for {service}: {price.valid_until}")


def gemini_cost_usd(
    model: str, *, input_tokens: int, output_tokens: int, on: date | None = None
) -> float:
    _tokens(input_tokens, output_tokens)
    for suffix in ("input", "output"):
        ensure_price_valid(f"google.{model}.{suffix}", on=on)
    return (
        input_tokens * PRICES[f"google.{model}.input"].unit_price_usd
        + output_tokens * PRICES[f"google.{model}.output"].unit_price_usd
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


_RESERVATION_FIELDS = {
    "ts",
    "kind",
    "reservation_id",
    "purpose",
    "supplier",
    "model",
    "task_id",
    "estimate_usd",
}


def _is_reservation(row: object) -> bool:
    return isinstance(row, dict) and row.get("kind") == "reservation"


def _validate_reservation(row: object, *, grading: bool) -> None:
    if not isinstance(row, dict) or set(row) != _RESERVATION_FIELDS:
        raise ValueError("invalid cost ledger reservation fields")
    purpose = row["purpose"]
    if not isinstance(purpose, str) or purpose not in PURPOSE_SUPPLIER:
        raise ValueError("invalid cost ledger purpose")
    if (purpose == "grading") != grading or row["supplier"] != PURPOSE_SUPPLIER[
        purpose
    ]:
        raise ValueError("invalid cost ledger routing")
    if not isinstance(row["reservation_id"], str) or not row["reservation_id"]:
        raise ValueError("invalid reservation id")
    _amount(row["estimate_usd"])
    if not isinstance(row["model"], str) or not row["model"]:
        raise ValueError("invalid model")
    if row["task_id"] is not None and not isinstance(row["task_id"], str):
        raise ValueError("invalid task_id")
    if not isinstance(row["ts"], str):
        raise TypeError("invalid timestamp")
    if datetime.fromisoformat(row["ts"]).utcoffset() != UTC.utcoffset(None):
        raise ValueError("timestamp must be UTC")


def _validate(row: object, *, grading: bool) -> None:
    # 정산 행은 예약 ID를 가질 수 있다(예약 도입 전 행은 없다).
    if not isinstance(row, dict) or set(row) not in (
        _FIELDS,
        _FIELDS | {"reservation_id"},
    ):
        raise ValueError("invalid cost ledger fields")
    if "reservation_id" in row and (
        not isinstance(row["reservation_id"], str) or not row["reservation_id"]
    ):
        raise ValueError("invalid reservation id")
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

    def _read_rows(self, source, *, grading: bool) -> list[dict]:
        source.seek(0)
        rows = []
        for number, line in enumerate(source, 1):
            try:
                row = json.loads(line)
                if _is_reservation(row):
                    _validate_reservation(row, grading=grading)
                else:
                    _validate(row, grading=grading)
            except (ValueError, TypeError, KeyError):
                raise ValueError(f"Invalid cost ledger line {number}") from None
            rows.append(row)
        return rows

    @staticmethod
    def _tally(rows: list[dict], supplier: str) -> tuple[Decimal, Decimal]:
        """(정산된 실제 금액, 아직 정산되지 않은 예약 추정액)."""
        settled_ids = {
            row["reservation_id"]
            for row in rows
            if not _is_reservation(row) and "reservation_id" in row
        }
        settled = Decimal(0)
        pending = Decimal(0)
        for row in rows:
            if row["supplier"] != supplier:
                continue
            if _is_reservation(row):
                if row["reservation_id"] not in settled_ids:
                    pending += _amount(row["estimate_usd"])
            else:
                settled += _amount(row["cost_usd"])
        return settled, pending

    def _snapshot(self, supplier: str) -> tuple[Decimal, Decimal]:
        if supplier not in SUPPLIER_CAPS_USD:
            raise ValueError("unknown supplier")
        self._require_ledgers()
        grading = supplier == "gemini"
        with self._open(self.root / _FILENAMES[grading], "r") as source:
            fcntl.flock(source, fcntl.LOCK_SH)
            return self._tally(self._read_rows(source, grading=grading), supplier)

    def total_usd(self, supplier: str) -> float:
        """정산된 실제 누적액(미정산 예약 제외)."""
        return float(self._snapshot(supplier)[0])

    def committed_usd(self, supplier: str) -> float:
        """실제 누적액 + 미정산 예약액. 상한 검사는 이 값을 쓴다."""
        settled, pending = self._snapshot(supplier)
        return float(settled + pending)

    def check_before_call(self, *, purpose: str, estimate_usd: float) -> None:
        """예약 없이 읽기만 하는 검사. 실제 호출은 reserve()로 원자적으로 예약한다."""
        supplier = PURPOSE_SUPPLIER[purpose]
        estimate = _amount(estimate_usd)
        settled, pending = self._snapshot(supplier)
        self._enforce(supplier, settled + pending, estimate)

    @staticmethod
    def _enforce(supplier: str, committed: Decimal, estimate: Decimal) -> None:
        cap = Decimal(str(SUPPLIER_CAPS_USD[supplier]))
        if committed + estimate > cap:
            raise BudgetExceededError(
                f"{supplier}: total={committed}, estimate={estimate}, cap={cap}"
            )

    def reserve(
        self,
        *,
        purpose: str,
        model: str,
        task_id: str | None,
        estimate_usd: float,
    ) -> str:
        """상한 검사와 예약 행 기록을 파일 잠금 안에서 한 번에 한다.

        정산되지 않은 예약(프로세스가 죽은 경우 등)은 삭제하지 않고 상한 쪽으로
        보수적으로 계속 센다.
        """
        self._require_ledgers()
        supplier = PURPOSE_SUPPLIER[purpose]
        grading = purpose == "grading"
        estimate = _amount(estimate_usd)
        reservation_id = uuid.uuid4().hex
        row = {
            "ts": datetime.now(UTC).isoformat(),
            "kind": "reservation",
            "reservation_id": reservation_id,
            "purpose": purpose,
            "supplier": supplier,
            "model": model,
            "task_id": task_id,
            "estimate_usd": float(estimate),
        }
        _validate_reservation(row, grading=grading)
        line = json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
        with self._open(self.root / _FILENAMES[grading], "r+") as target:
            fcntl.flock(target, fcntl.LOCK_EX)
            settled, pending = self._tally(
                self._read_rows(target, grading=grading), supplier
            )
            self._enforce(supplier, settled + pending, estimate)
            target.seek(0, os.SEEK_END)
            target.write(line)
            target.flush()
            os.fsync(target.fileno())
        return reservation_id

    def settle(
        self,
        reservation_id: str,
        *,
        purpose: str,
        model: str,
        task_id: str | None,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        cost_usd: float,
    ) -> None:
        """예약 ID로 실제 금액을 정산한다(실패한 호출은 0 또는 확인된 사용량)."""
        self._require_ledgers()
        grading = purpose == "grading"
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
            "reservation_id": reservation_id,
        }
        _validate(row, grading=grading)
        line = json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
        with self._open(self.root / _FILENAMES[grading], "r+") as target:
            fcntl.flock(target, fcntl.LOCK_EX)
            rows = self._read_rows(target, grading=grading)
            reserved = [
                r
                for r in rows
                if _is_reservation(r) and r["reservation_id"] == reservation_id
            ]
            if len(reserved) != 1 or reserved[0]["purpose"] != purpose:
                raise ValueError("unknown reservation")
            if any(
                not _is_reservation(r) and r.get("reservation_id") == reservation_id
                for r in rows
            ):
                raise ValueError("reservation already settled")
            target.seek(0, os.SEEK_END)
            target.write(line)
            target.flush()
            os.fsync(target.fileno())


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
