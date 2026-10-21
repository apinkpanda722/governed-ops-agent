"""Sticky conversation routing and body-free rollout measurements."""

import asyncio
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from govops.agent.graph import get_task
from govops.api.conversations import TurnIndex
from govops.api.tasks import TaskService
from govops.api.variant import VariantStore
from govops.rollout import policy, store

_ID = re.compile(r"[0-9a-f]{32}")
_WARNED: set[str] = set()
_MISSING: set[str] = set()


def _now():
    return datetime.now(UTC)


def _warn_once(code):
    if code not in _WARNED:
        _WARNED.add(code)
        print(f"WARN {code}", file=sys.stderr)


class VariantFilteredTurnIndex:
    """Filter shared listings before a service takes its own conversation lock."""

    def __init__(
        self, inner: TurnIndex, variants: VariantStore, variant: Literal["v1", "v2"]
    ):
        self.inner, self.variants, self.variant = inner, variants, variant

    async def add(self, *, turn_id, conversation_id, owner_sub, turn_index, created_at):
        return await self.inner.add(
            turn_id=turn_id,
            conversation_id=conversation_id,
            owner_sub=owner_sub,
            turn_index=turn_index,
            created_at=created_at,
        )

    async def list_for_owner(self, owner_sub: str, limit: int) -> list[dict]:
        rows = await self.inner.list_for_owner(owner_sub, limit)
        variants = await self.variants.get_many(
            [row["conversation_id"] for row in rows]
        )
        return [
            row
            for row in rows
            if variants.get(row["conversation_id"], "v1") == self.variant
        ]

    async def turn_ids_of(self, conversation_id, owner_sub):
        return await self.inner.turn_ids_of(conversation_id, owner_sub)

    async def conversation_of(self, turn_id):
        return await self.inner.conversation_of(turn_id)

    async def set_awaiting(self, turn_id, awaiting: bool):
        return await self.inner.set_awaiting(turn_id, awaiting)

    async def awaiting(self):
        rows = await self.inner.awaiting()
        variants = await self.variants.get_many([ident for _, ident in rows])
        return [row for row in rows if variants.get(row[1], "v1") == self.variant]

    async def mark_deleted(self, conversation_id: str, owner_sub: str, deleted_at: str):
        return await self.inner.mark_deleted(conversation_id, owner_sub, deleted_at)

    async def is_deleted(self, conversation_id: str):
        return await self.inner.is_deleted(conversation_id)

    async def deleted_conversation_ids(self):
        return await self.inner.deleted_conversation_ids()

    async def delete_conversations(self, conversation_ids):
        return await self.inner.delete_conversations(conversation_ids)


class RecordingClassifier:
    def __init__(self, inner, *, directory: Path, monotonic=time.monotonic, now=_now):
        self.inner, self.directory = inner, directory
        self.monotonic, self.now = monotonic, now

    async def _record(self, task_id, status, started_ms, ended_ms):
        if not _ID.fullmatch(task_id):
            return
        try:
            await asyncio.to_thread(
                store.append_classify,
                self.directory,
                {
                    "ts": self.now().astimezone(UTC).isoformat(),
                    "turn_id": task_id,
                    "status": status,
                    "started_ms": started_ms,
                    "ended_ms": ended_ms,
                },
            )
        except (OSError, ValueError):
            _warn_once("rollout_classify_record_failed")

    async def classify(self, *, request, task_id, user_id):
        started_ms = self.monotonic() * 1000
        try:
            result = await self.inner.classify(
                request=request, task_id=task_id, user_id=user_id
            )
        except BaseException:
            await self._record(task_id, "raised", started_ms, self.monotonic() * 1000)
            raise
        await self._record(task_id, result.status, started_ms, self.monotonic() * 1000)
        return result


class RoutedTaskService:
    def __init__(
        self,
        v1: TaskService,
        v2: TaskService,
        *,
        turns: TurnIndex,
        variants: VariantStore,
        directory: Path,
        clock=None,
    ):
        self.v1, self.v2 = v1, v2
        self.turns, self.variants = turns, variants
        self.directory, self.clock = directory, clock or _now
        self._assignment_lock = asyncio.Lock()

    @property
    def audit(self):
        return self.v1.audit

    def _timestamp(self):
        return self.clock().astimezone(UTC).isoformat()

    async def _exposure(self, row):
        try:
            await asyncio.to_thread(store.append_exposure, self.directory, row)
        except (OSError, ValueError):
            _warn_once("rollout_exposure_record_failed")

    async def _window(self, variant, service):
        outcomes, owners = [], {}
        for ident in await self.variants.recent(variant, policy.CANDIDATE_LIMIT):
            state = await get_task(service.graph, ident)
            if state is None or state.get("status") in {"running", "awaiting_approval"}:
                continue
            outcomes.append(
                policy.ConversationOutcome(
                    ident, state.get("status") == "blocked_input", {}
                )
            )
            owners[ident] = state.get("user_id")
            if len(outcomes) == policy.WINDOW:
                break
        return outcomes, owners

    async def _evaluate_rollback(self, state):
        v2, owners = await self._window("v2", self.v2)
        v1, _ = await self._window("v1", self.v1)
        try:
            rows = await asyncio.to_thread(
                store.read_classify_tail, self.directory, policy.CLASSIFY_TAIL
            )
        except (OSError, ValueError):
            return await self._rollback(state, policy.RECORDS_INVALID_CODE)
        # Map turns per window conversation (at most WINDOW lookups, addendum-1 §3).
        outcomes = {}
        for outcome in v2:
            for turn_id, _ in await self.turns.turn_ids_of(
                outcome.conversation_id, owners[outcome.conversation_id]
            ):
                outcomes[turn_id] = outcome
        for row in rows:
            outcome = outcomes.get(row["turn_id"])
            if outcome is not None:
                outcome.turns.setdefault(row["turn_id"], []).append(
                    policy.ClassifyCall(
                        row["status"], row["started_ms"], row["ended_ms"]
                    )
                )
        try:
            evaluation = policy.evaluate(v2, v1)
        except ValueError:
            return await self._rollback(state, policy.RECORDS_INVALID_CODE)
        if evaluation.action != "rollback":
            return state
        return await self._rollback(state, evaluation.reasons[0])

    async def _rollback(self, state, reason):
        ts = self._timestamp()
        stopped = policy.RolloutState(0, True, ts, reason)
        try:
            await asyncio.to_thread(store.write_state, self.directory, stopped)
        except (OSError, ValueError):
            # Even when persistence is unavailable, this assignment stays on v1.
            return stopped
        await self._exposure(
            {
                "event": "rollback",
                "ts": ts,
                "reason": reason,
                "pct_bp": state.pct_bp,
            }
        )
        return await asyncio.to_thread(store.read_state, self.directory)

    async def _start(self, method, user_id, request):
        async with self._assignment_lock:
            state = await asyncio.to_thread(store.read_state, self.directory)
            if not state.kill and state.pct_bp > 0:
                state = await self._evaluate_rollback(state)
            variant = policy.assign(user_id, state)
            service = self.v2 if variant == "v2" else self.v1
            result = await getattr(service, method)(user_id, request)
            ident = result if method == "start" else result["conversation_id"]
            ts, bucket = self._timestamp(), policy.bucket(user_id)
            try:
                await self.variants.put(
                    conversation_id=ident,
                    variant=variant,
                    bucket=bucket,
                    pct_bp=state.pct_bp,
                    assigned_at=ts,
                )
            except Exception:  # noqa: BLE001 - assignment survives storage failures
                _warn_once("rollout_variant_write_failed")
            await self._exposure(
                {
                    "event": "assigned",
                    "ts": ts,
                    "conversation_id": ident,
                    "user_sha256": policy.user_digest(user_id),
                    "bucket": bucket,
                    "variant": variant,
                    "pct_bp": state.pct_bp,
                    "kill": state.kill,
                }
            )
            return result

    async def start(self, user_id, request) -> str:
        return await self._start("start", user_id, request)

    async def start_conversation(self, user_id, request) -> dict:
        return await self._start("start_conversation", user_id, request)

    async def _service(self, conversation_id):
        if not _ID.fullmatch(conversation_id):
            return self.v1
        variants = await self.variants.get_many([conversation_id])
        if variants.get(conversation_id) == "v2":
            return self.v2
        if (
            conversation_id not in variants
            and conversation_id not in _MISSING
            and await get_task(self.v1.graph, conversation_id) is not None
            # Recheck after the graph read: another request may have recorded it.
            and conversation_id not in _MISSING
        ):
            _MISSING.add(conversation_id)
            await self._exposure(
                {
                    "event": "variant_missing",
                    "ts": self._timestamp(),
                    "conversation_id": conversation_id,
                }
            )
        return self.v1

    async def _task_service(self, task_id):
        conversation_id = (
            await self.turns.conversation_of(task_id) or task_id
            if _ID.fullmatch(task_id)
            else task_id
        )
        return await self._service(conversation_id)

    async def add_turn(self, user_id, conversation_id, request):
        service = await self._service(conversation_id)
        return await service.add_turn(user_id, conversation_id, request)

    async def delete_conversation(self, user_id, conversation_id) -> dict:
        service = await self._service(conversation_id)
        return await service.delete_conversation(user_id, conversation_id)

    async def conversations(self, user_id, limit):
        first = await self.v1.conversations(user_id, limit)
        second = await self.v2.conversations(user_id, limit)
        rows = first["conversations"] + second["conversations"]
        rows.sort(key=lambda row: row["conversation_id"])
        rows.sort(key=lambda row: row["updated_at"], reverse=True)
        return {"conversations": rows[:limit]}

    async def conversation_view(self, user_id, conversation_id):
        service = await self._service(conversation_id)
        return await service.conversation_view(user_id, conversation_id)

    async def progress(self, user_id, conversation_id):
        service = await self._service(conversation_id)
        return await service.progress(user_id, conversation_id)

    async def audit_view(self, user_id, conversation_id) -> dict:
        service = await self._service(conversation_id)
        return await service.audit_view(user_id, conversation_id)

    async def audit_verify(self) -> dict:
        return await self.v1.audit_verify()

    async def approvals(self, user_id):
        first = await self.v1.approvals(user_id)
        second = await self.v2.approvals(user_id)
        rows = first["approvals"] + second["approvals"]
        rows.sort(key=lambda row: (row["expires_at"], row["task_id"]))
        return {"approvals": rows}

    async def get(self, user_id, task_id):
        service = await self._task_service(task_id)
        return await service.get(user_id, task_id)

    async def approve(self, user_id, task_id, *, decision, draft_sha256, edited=None):
        service = await self._task_service(task_id)
        return await service.approve(
            user_id,
            task_id,
            decision=decision,
            draft_sha256=draft_sha256,
            edited=edited,
        )

    async def startup_cleanup(self):
        return await self.v1.startup_cleanup()

    async def aclose(self) -> None:
        try:
            await self.v1.aclose()
        finally:
            await self.v2.aclose()
