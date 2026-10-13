"""Find old or deletion-marked inactive threads; deletion requires explicit apply."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from langgraph.checkpoint.base import BaseCheckpointSaver

RETENTION_DAYS = 7


@dataclass(frozen=True)
class PruneResult:
    cutoff: str
    scanned: int
    candidates: tuple[str, ...]
    active: int
    deleted: int
    deleted_ids: tuple[str, ...]


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


async def prune_checkpoints(
    saver: BaseCheckpointSaver,
    *,
    now: datetime,
    apply: bool = False,
    retention_days: int = RETENTION_DAYS,
    deleted_conversation_ids: Collection[str] = (),
) -> PruneResult:
    """Select each thread by latest ts, preserving active or unknown state."""
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("now must be timezone-aware UTC")
    cutoff = now - timedelta(days=retention_days)
    deleted_conversations = set(deleted_conversation_ids)
    latest: dict[str, tuple[datetime, object, object]] = {}
    unknown_ts: set[str] = set()
    async for item in saver.alist(None):
        thread_id = item.config["configurable"]["thread_id"]
        ts = _timestamp(item.checkpoint.get("ts"))
        if ts is None:
            # Without a timestamp we cannot prove which state is latest.
            unknown_ts.add(thread_id)
            continue
        if thread_id not in latest or ts > latest[thread_id][0]:
            values = item.checkpoint.get("channel_values", {})
            latest[thread_id] = (ts, values.get("status"), values.get("expires_at"))

    candidates = []
    active = len(unknown_ts)
    for thread_id in sorted(latest):
        if thread_id in unknown_ts:
            continue
        ts, status, expires_at = latest[thread_id]
        expiry = _timestamp(expires_at)
        if (
            status is None
            or status == "running"
            or (status == "awaiting_approval" and (expiry is None or now < expiry))
        ):
            active += 1
        elif ts < cutoff or thread_id in deleted_conversations:
            candidates.append(thread_id)

    deleted_ids = []
    if apply:
        for thread_id in candidates:
            try:
                await saver.adelete_thread(thread_id)
            except Exception:  # noqa: BLE001, S112 — continue silently to avoid exposing content
                continue
            deleted_ids.append(thread_id)
    return PruneResult(
        cutoff=cutoff.isoformat(),
        scanned=len(latest.keys() | unknown_ts),
        candidates=tuple(candidates),
        active=active,
        deleted=len(deleted_ids),
        deleted_ids=tuple(deleted_ids),
    )
