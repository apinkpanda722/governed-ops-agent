"""체크포인터 목록 조회(alist) 안에서 같은 체크포인터를 다시 부르면 교착한다.

AsyncPostgresSaver는 `_cursor()`에서 `self.lock`(asyncio.Lock)을 쥔 채 alist의 항목을 yield하고,
aget_tuple·aput·aput_writes·adelete_thread도 같은 잠금을 기다린다. InMemorySaver에는 이 잠금이
없어 이 교착을 재현하지 못한다. 이 파일의 가짜 saver는 그 잠금 계약만 재현한다.
"""

import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeTools, async_test, reply

from govops.agent.graph import build_graph
from govops.agent.retention import prune_checkpoints
from govops.api.conversations import InMemoryTurnIndex
from govops.api.retention import startup_prune
from govops.api.tasks import TaskService
from govops.audit.log import MemoryAuditLog

BOUND = 5.0  # 교착하면 이 시간 안에 끝나지 않는다. 정상 경로는 밀리초 단위다.


class LockedSaver(InMemorySaver):
    def __init__(self):
        super().__init__()
        self.lock = asyncio.Lock()

    async def alist(self, config, **kwargs):
        async with self.lock:
            async for item in super().alist(config, **kwargs):
                yield item

    async def aget_tuple(self, config):
        async with self.lock:
            return await super().aget_tuple(config)

    async def aput(self, *args, **kwargs):
        async with self.lock:
            return await super().aput(*args, **kwargs)

    async def aput_writes(self, *args, **kwargs):
        async with self.lock:
            return await super().aput_writes(*args, **kwargs)

    async def adelete_thread(self, thread_id):
        async with self.lock:
            return await super().adelete_thread(thread_id)


class Chat:
    async def complete(self, **kwargs):
        return reply("합성 답변")


async def seed(saver, users=("a", "b", "c")):
    """완료된 대화 여러 개와 재시작으로 `running`에 남은 대화 하나를 만든다."""
    graph = build_graph(Chat(), FakeTools(), saver)
    service = TaskService(graph, turns=InMemoryTurnIndex())
    ids = [await service.start(user, "합성 요청") for user in users]
    await asyncio.gather(*service._tasks)
    crashed = ids[-1]
    await graph.aupdate_state(
        {"configurable": {"thread_id": crashed}},
        {"status": "running"},
        as_node="model",
    )
    await service.aclose()
    return graph, ids, crashed


@async_test
async def test_fake_saver_reproduces_the_nested_call_deadlock():
    saver = LockedSaver()
    graph, ids, _ = await seed(saver)
    config = {"configurable": {"thread_id": ids[0]}}
    # alist를 순회하는 중에 같은 saver를 부르면 잠금 때문에 끝나지 않는다.
    with pytest.raises(TimeoutError):
        async for _item in saver.alist(None):
            await asyncio.wait_for(graph.aget_state(config), 0.5)
    # 순회가 끝난 뒤에는 같은 호출이 곧바로 끝난다.
    assert (await asyncio.wait_for(graph.aget_state(config), BOUND)).values


@async_test
async def test_startup_cleanup_finishes_with_a_lock_holding_alist():
    saver = LockedSaver()
    graph, ids, crashed = await seed(saver)
    fresh = TaskService(graph, turns=InMemoryTurnIndex())
    await asyncio.wait_for(fresh.startup_cleanup(), BOUND)
    done = await fresh.get("a", ids[0])
    assert done["status"] == "completed"
    interrupted = await fresh.get("c", crashed)
    assert interrupted["status"] == "interrupted"
    assert interrupted["effect_result"] == "unknown"


@async_test
async def test_startup_cleanup_visits_every_thread_exactly_once():
    saver = LockedSaver()
    graph, ids, _ = await seed(saver, users=("a", "b", "c", "d"))
    fresh = TaskService(graph, turns=InMemoryTurnIndex())
    visited = []
    original = fresh._update

    async def record(thread, values):
        visited.append(thread)
        return await original(thread, values)

    fresh._update = record
    await asyncio.wait_for(fresh.startup_cleanup(), BOUND)
    # `running`으로 남은 대화는 정확히 하나이고 그 대화만 갱신된다.
    assert visited == [ids[-1]]


@async_test
async def test_prune_and_startup_prune_finish_with_a_lock_holding_alist():
    from datetime import UTC, datetime, timedelta

    saver = LockedSaver()
    graph, ids, crashed = await seed(saver)
    now = datetime.now(UTC) + timedelta(days=8)
    dry = await asyncio.wait_for(prune_checkpoints(saver, now=now), BOUND)
    assert set(dry.candidates) == set(ids) - {crashed}

    service = TaskService(graph, turns=InMemoryTurnIndex(), audit=MemoryAuditLog())
    await asyncio.wait_for(startup_prune(service, saver, service.turns), BOUND)
