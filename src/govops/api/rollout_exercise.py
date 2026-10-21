"""Body-free local demonstration of rollout routing and automatic rollback."""

import argparse
import asyncio
import tempfile
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from govops.agent.graph import build_graph
from govops.api.conversations import InMemoryTurnIndex
from govops.api.routed import (
    RecordingClassifier,
    RoutedTaskService,
    VariantFilteredTurnIndex,
)
from govops.api.tasks import TaskService
from govops.api.variant import InMemoryVariantStore
from govops.audit.log import MemoryAuditLog
from govops.gateway.client import ChatResult, ClassifyResult, Usage
from govops.rollout import policy, store

REQUEST = (
    "합성 운영 기록의 처리 상태와 확인 내역을 읽고 요약해 주세요. "
    "이 문장은 로컬 롤아웃 시연을 위한 합성 요청입니다."
)
ANSWER = "로컬 롤아웃 시연 전용 합성 답변입니다."


class FakeChat:
    async def complete(self, **kwargs):
        return ChatResult(
            {"role": "assistant", "content": ANSWER, "tool_calls": []},
            "fake",
            Usage(0, 0, 0),
            0.0,
        )


class FakeTools:
    async def call(self, name, arguments, *, user_id):
        raise RuntimeError("exercise_tools_unused")


class FakeClassifier:
    def __init__(self, status):
        self.status = status

    async def classify(self, *, request, task_id, user_id):
        return ClassifyResult("safe", self.status, 0, 0, 0.0)


class _ExerciseFailure(Exception):
    pass


def _require(condition, code):
    if not condition:
        raise _ExerciseFailure(code)


def _router(directory: Path, status: str) -> RoutedTaskService:
    saver, turns, variants = (
        InMemorySaver(),
        InMemoryTurnIndex(),
        InMemoryVariantStore(),
    )
    chat, tools, audit = FakeChat(), FakeTools(), MemoryAuditLog()
    services = []
    for variant in ("v1", "v2"):
        graph = build_graph(
            chat,
            tools,
            saver,
            audit=audit,
            exploration_budget=4,
            semantic=RecordingClassifier(FakeClassifier(status), directory=directory)
            if variant == "v2"
            else None,
        )
        services.append(
            TaskService(
                graph,
                turns=VariantFilteredTurnIndex(turns, variants, variant),
                audit=audit,
            )
        )
    return RoutedTaskService(
        *services, turns=turns, variants=variants, directory=directory
    )


async def _finish(router, user, result, code):
    await asyncio.wait_for(asyncio.gather(*router.v1._tasks, *router.v2._tasks), 10)
    view = await router.get(user, result["turn_id"])
    _require(view is not None and view["status"] == "completed", code)


async def _create(router, user, code):
    result = await router.start_conversation(user, REQUEST)
    await _finish(router, user, result, code)
    return result["conversation_id"]


async def _sticky(router, directory, user, ident):
    result = await router.add_turn(user, ident, REQUEST)
    await _finish(router, user, result, "sticky_broken")
    calls = store.read_classify_tail(directory)
    _require(
        result["turn_id"] != ident
        and sum(row["turn_id"] == result["turn_id"] for row in calls) == 1,
        "sticky_broken",
    )


async def _exercise_path(*, directory: Path, rollback: bool):
    store.write_state(
        directory, policy.RolloutState(10000 if rollback else 5000, False, None, None)
    )
    router = _router(directory, "timeout" if rollback else "ok")
    try:
        conversations = []
        code = "rollback_missing" if rollback else "assignment_too_few"
        for index in range(20 if rollback else 60):
            user = f"demo-exercise-g{index}"
            ident = await _create(router, user, code)
            conversations.append((user, ident))
        if rollback:
            last = await _create(router, "demo-exercise-g20", "rollback_missing")
        state = store.read_state(directory)
        rows = store.read_exposures(directory)
        assignments = [row for row in rows if row["event"] == "assigned"]
        v2 = [row["conversation_id"] for row in assignments if row["variant"] == "v2"]
        v1 = [row["conversation_id"] for row in assignments if row["variant"] == "v1"]
        rollbacks = sum(row["event"] == "rollback" for row in rows)
        if rollback:
            _require(state.kill and state.pct_bp == 0, "rollback_missing")
            _require(state.reason == "rollback_classify_abnormal", "reason_mismatch")
            _require(rollbacks == 1, "rollback_row_count")
            _require(last in v1, "last_not_v1")
            _require(
                len(v2) == 20 and {ident for _, ident in conversations} == set(v2),
                "assignment_too_few",
            )
        else:
            _require(not state.kill, "kill_unexpected")
            _require(len(v2) >= 20 and len(v1) >= 20, "assignment_too_few")
            _require(rollbacks == 0, "rollback_row_count")
        user, ident = next(pair for pair in conversations if pair[1] == v2[0])
        await _sticky(router, directory, user, ident)
    finally:
        await asyncio.wait_for(router.aclose(), 5)


async def run_exercise(*, root: Path) -> list[tuple[str, bool, str | None]]:
    results = []
    for name, rollback in (
        ("exercise_pass_path", False),
        ("exercise_rollback_path", True),
    ):
        try:
            await asyncio.wait_for(
                _exercise_path(directory=root / name, rollback=rollback), 60
            )
        except _ExerciseFailure as exc:
            results.append((name, False, str(exc)))
        except TimeoutError:
            code = "rollback_missing" if rollback else "assignment_too_few"
            results.append((name, False, code))
        else:
            results.append((name, True, None))
    return results


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    with tempfile.TemporaryDirectory() as temporary:
        results = asyncio.run(run_exercise(root=Path(temporary)))
    for name, passed, code in results:
        print(f"PASS {name}" if passed else f"FAIL {name} {code}")
    return 0 if all(passed for _, passed, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
