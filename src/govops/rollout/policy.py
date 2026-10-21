"""Pure policy frozen by hypotheses-m8-rollout.md; no runtime I/O."""

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

SALT: str = "govops-m8-rollout-v1"
WINDOW: int = 20
MIN_SAMPLE: int = 20
CANDIDATE_LIMIT: int = 60
CLASSIFY_TAIL: int = 5000
ABNORMAL_RATE_MAX: float = 0.05
BLOCK_DIFF_MAX: float = 0.10
P95_MS_MAX: float = 8000.0
ABNORMAL_STATUSES: frozenset[str] = frozenset(
    {"timeout", "rate_limited", "error", "parse", "raised"}
)
CLASSIFY_STATUSES: frozenset[str] = ABNORMAL_STATUSES | {"ok", "v1_blocked"}
ROLLBACK_CODES: tuple[str, ...] = (
    "rollback_no_classify_calls",
    "rollback_classify_abnormal",
    "rollback_block_diff",
    "rollback_classify_p95",
)


@dataclass(frozen=True)
class RolloutState:
    pct_bp: int
    kill: bool
    rolled_back_at: str | None
    reason: str | None


OFF: RolloutState = RolloutState(0, False, None, None)


def user_digest(user_label: str) -> str:
    return hashlib.sha256((SALT + user_label).encode("utf-8")).hexdigest()


def bucket(user_label: str) -> int:
    return int(user_digest(user_label)[:8], 16) % 10000


def assign(user_label: str, state: RolloutState) -> Literal["v1", "v2"]:
    if type(state.pct_bp) is not int or not 0 <= state.pct_bp <= 10000:
        raise ValueError("invalid_pct_bp")
    return "v2" if not state.kill and bucket(user_label) < state.pct_bp else "v1"


@dataclass(frozen=True)
class ClassifyCall:
    status: str
    started_ms: float
    ended_ms: float


@dataclass(frozen=True)
class ConversationOutcome:
    conversation_id: str
    blocked_input: bool
    turns: Mapping[str, Sequence[ClassifyCall]]


@dataclass(frozen=True)
class Evaluation:
    action: Literal["none", "rollback"]
    reasons: tuple[str, ...]
    notes: tuple[str, ...]
    abnormal_rate: float | None
    block_diff: float | None
    p95_ms: float | None


def union_ms(calls: Sequence[ClassifyCall]) -> float:
    """Length of the interval union, including overlapping concurrent calls once."""
    intervals = sorted((call.started_ms, call.ended_ms) for call in calls)
    if not intervals:
        return 0.0
    total = 0.0
    start, end = intervals[0]
    for next_start, next_end in intervals[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += end - start
            start, end = next_start, next_end
    return float(total + end - start)


def nearest_rank_p95(values: Sequence[float]) -> float | None:
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * 0.95) - 1] if ordered else None


def evaluate(
    v2: Sequence[ConversationOutcome], v1: Sequence[ConversationOutcome]
) -> Evaluation:
    """Evaluate caller-selected windows, preserving registered reason order."""
    v2, v1 = v2[:WINDOW], v1[:WINDOW]
    if len(v2) < MIN_SAMPLE:
        return Evaluation("none", (), ("insufficient_v2_sample",), None, None, None)

    calls = [call for outcome in v2 for turn in outcome.turns.values() for call in turn]
    if any(call.status not in CLASSIFY_STATUSES for call in calls):
        raise ValueError("invalid_classify_status")
    reasons: list[str] = []
    notes: list[str] = []
    abnormal_rate = None
    p95_ms = None
    if not calls:
        reasons.append(ROLLBACK_CODES[0])
    else:
        abnormal_rate = sum(call.status in ABNORMAL_STATUSES for call in calls) / len(
            calls
        )
        if abnormal_rate > ABNORMAL_RATE_MAX:
            reasons.append(ROLLBACK_CODES[1])
        p95_ms = nearest_rank_p95(
            [union_ms(turn) for outcome in v2 for turn in outcome.turns.values()]
        )

    block_diff = None
    if len(v1) < MIN_SAMPLE:
        notes.append("insufficient_v1_control")
    else:
        # Subtract integer counts first: 8/20 - 6/20 must equal 0.10 exactly.
        block_diff = (
            sum(outcome.blocked_input for outcome in v2) * len(v1)
            - sum(outcome.blocked_input for outcome in v1) * len(v2)
        ) / (len(v2) * len(v1))
        if block_diff > BLOCK_DIFF_MAX:
            reasons.append(ROLLBACK_CODES[2])
    if p95_ms is not None and p95_ms > P95_MS_MAX:
        reasons.append(ROLLBACK_CODES[3])
    return Evaluation(
        "rollback" if reasons else "none",
        tuple(reasons),
        tuple(notes),
        abnormal_rate,
        block_diff,
        p95_ms,
    )
