"""M8-only append-only reservation accounting, independent of supplier ledgers."""

import fcntl
import json
import math
import os
import uuid
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

STAGE_CAPS_USD = {"calibration": 0.8, "validation": 0.6, "measurement": 0.6}
TOTAL_CAP_USD = 2.0
DEFAULT_PATH = (
    Path(__file__).resolve().parents[3] / "data/interim/m8-grounding/spend.jsonl"
)


class SpendCapError(RuntimeError):
    """A stage or the total M8 allocation would be exceeded."""


def _amount(value: float) -> Decimal:
    if type(value) not in (int, float):
        raise TypeError("invalid M8 spend amount")
    if not math.isfinite(value) or value < 0:
        raise ValueError("invalid M8 spend amount")
    return Decimal(str(value))


def _stage(stage: str) -> None:
    if not isinstance(stage, str) or stage not in STAGE_CAPS_USD:
        raise ValueError("invalid M8 spend stage")


class M8Spend:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "r+", encoding="utf-8") as target:
            fcntl.flock(target, fcntl.LOCK_EX)
            os.fchmod(target.fileno(), 0o600)
            yield target

    @staticmethod
    def _read(target) -> dict[str, dict]:
        target.seek(0)
        reservations: dict[str, dict] = {}
        failed = False
        try:
            for line in target:
                if not line.endswith("\n"):
                    raise ValueError
                row = json.loads(line)
                if not isinstance(row, dict) or set(row) != {
                    "kind",
                    "reservation_id",
                    "stage",
                    "amount_usd",
                }:
                    raise ValueError
                _stage(row["stage"])
                _amount(row["amount_usd"])
                rid = row["reservation_id"]
                if not isinstance(rid, str) or not rid:
                    raise ValueError
                if row["kind"] == "reservation" and rid not in reservations:
                    reservations[rid] = row | {"settled": False}
                elif (
                    row["kind"] == "settlement"
                    and rid in reservations
                    and not reservations[rid]["settled"]
                    and reservations[rid]["stage"] == row["stage"]
                ):
                    reservations[rid] = row | {"settled": True}
                else:
                    raise ValueError
        except (ValueError, TypeError, KeyError, UnicodeError):
            failed = True
        if failed:
            raise ValueError("invalid M8 spend ledger")
        return reservations

    @staticmethod
    def _total(rows: dict[str, dict], stage: str | None = None) -> Decimal:
        return sum(
            (
                _amount(row["amount_usd"])
                for row in rows.values()
                if stage is None or row["stage"] == stage
            ),
            Decimal(0),
        )

    @classmethod
    def _check(cls, rows: dict[str, dict], stage: str, estimate: Decimal) -> None:
        if cls._total(rows, stage) + estimate > Decimal(str(STAGE_CAPS_USD[stage])):
            raise SpendCapError("m8_stage_cap_exceeded")
        if cls._total(rows) + estimate > Decimal(str(TOTAL_CAP_USD)):
            raise SpendCapError("m8_total_cap_exceeded")

    @staticmethod
    def _append(target, row: dict) -> None:
        target.seek(0, os.SEEK_END)
        target.write(json.dumps(row, allow_nan=False, separators=(",", ":")) + "\n")
        target.flush()
        os.fsync(target.fileno())

    def check(self, stage: str, estimate_usd: float) -> None:
        _stage(stage)
        estimate = _amount(estimate_usd)
        with self._locked() as target:
            self._check(self._read(target), stage, estimate)

    def reserve(self, stage: str, estimate_usd: float) -> str:
        _stage(stage)
        estimate = _amount(estimate_usd)
        rid = uuid.uuid4().hex
        with self._locked() as target:
            self._check(self._read(target), stage, estimate)
            self._append(
                target,
                {
                    "kind": "reservation",
                    "reservation_id": rid,
                    "stage": stage,
                    "amount_usd": float(estimate),
                },
            )
        return rid

    def settle(self, reservation_id: str, actual_usd: float) -> None:
        actual = _amount(actual_usd)
        with self._locked() as target:
            rows = self._read(target)
            if reservation_id not in rows or rows[reservation_id]["settled"]:
                raise ValueError("invalid M8 spend settlement")
            self._append(
                target,
                {
                    "kind": "settlement",
                    "reservation_id": reservation_id,
                    "stage": rows[reservation_id]["stage"],
                    "amount_usd": float(actual),
                },
            )

    def total_usd(self, stage: str | None = None) -> float:
        if stage is not None:
            _stage(stage)
        with self._locked() as target:
            return float(self._total(self._read(target), stage))
