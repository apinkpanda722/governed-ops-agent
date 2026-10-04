"""Read human-authored confirmations; never publish or update them."""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from govops.eval.spec import TaskSpec, spec_hash

REVIEW_PATH = Path("data/eval/review/confirmed.json")


class ReviewFormatError(ValueError):
    """Invalid human review format (diagnostics omit input values)."""


@dataclass(frozen=True)
class ReviewReport:
    confirmed: list[str]
    rejected: list[str]
    needs_change: list[str]
    pending: list[str]
    stale: list[str]


def load_confirmed_rows(review_path: Path) -> dict[str, dict]:
    """사람이 작성한 확정본의 문항별 판정(읽기 전용). 파일이 없으면 {}, 형식이 틀리면 오류."""
    rows: dict = {}
    if review_path.exists():
        try:
            raw = json.loads(review_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or set(raw) != {
                "reviewer",
                "reviewed_at",
                "tasks",
            }:
                raise ValueError
            if not isinstance(raw["reviewer"], str) or not raw["reviewer"].strip():
                raise ValueError
            if not isinstance(raw["reviewed_at"], str) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}", raw["reviewed_at"]
            ):
                raise ValueError
            date.fromisoformat(raw["reviewed_at"])
            rows = raw["tasks"]
            if not isinstance(rows, dict):
                raise TypeError
            for key, row in rows.items():
                if not re.fullmatch(r"T[1-6]-\d+", key) or not isinstance(row, dict):
                    raise ValueError
                if set(row) != {"status", "spec_sha256", "note"} or row[
                    "status"
                ] not in ("approved", "rejected", "needs_change"):
                    raise ValueError
                if (
                    not isinstance(row["spec_sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", row["spec_sha256"])
                    or not isinstance(row["note"], str)
                ):
                    raise ValueError
        except (OSError, ValueError, TypeError, KeyError):
            raise ReviewFormatError("invalid review format") from None
    return rows


def review_report(specs: Sequence[TaskSpec], review_path: Path) -> ReviewReport:
    rows = load_confirmed_rows(review_path)
    groups = {
        key: [] for key in ("confirmed", "rejected", "needs_change", "pending", "stale")
    }
    for spec in specs:
        row = rows.get(spec.task_id)
        if row is None:
            group = "pending"
        elif row["spec_sha256"] != spec_hash(spec):
            group = "stale"
        else:
            group = "confirmed" if row["status"] == "approved" else row["status"]
        groups[group].append(spec.task_id)
    return ReviewReport(**groups)


def confirmed_specs(specs: Sequence[TaskSpec], review_path: Path) -> list[TaskSpec]:
    try:
        ids = set(review_report(specs, review_path).confirmed)
    except ReviewFormatError:
        return []
    return [spec for spec in specs if spec.task_id in ids]
