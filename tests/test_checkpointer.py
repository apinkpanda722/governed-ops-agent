"""DB 접속 없이 saver의 왕복·설정·종료·안전한 오류를 검사한다."""

import asyncio
from contextlib import asynccontextmanager
from typing import TypedDict
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from govops.agent.checkpointer import AsyncPostgresSaver, open_checkpointer


class State(TypedDict):
    value: int


@pytest.mark.parametrize("url", [None, "", "memory"])
def test_memory_round_trip(url):
    asyncio.run(_memory_round_trip(url))


async def _memory_round_trip(url):
    async with open_checkpointer(url) as saver:
        assert isinstance(saver, InMemorySaver)
        builder = StateGraph(State)
        builder.add_node("increment", lambda state: {"value": state["value"] + 1})
        builder.add_edge(START, "increment")
        builder.add_edge("increment", END)
        graph = builder.compile(checkpointer=saver)
        config = {"configurable": {"thread_id": "synthetic-task"}}
        assert await graph.ainvoke({"value": 1}, config) == {"value": 2}
        assert (await graph.aget_state(config)).values == {"value": 2}


def test_postgres_lifecycle(monkeypatch):
    asyncio.run(_postgres_lifecycle(monkeypatch))


async def _postgres_lifecycle(monkeypatch):
    saver = AsyncMock()
    closed = False
    passed = []
    url = "postgresql://127.0.0.1:5432/govops"

    @asynccontextmanager
    async def fake(connection_string):
        nonlocal closed
        passed.append(connection_string)
        try:
            yield saver
        finally:
            closed = True

    monkeypatch.setattr(AsyncPostgresSaver, "from_conn_string", fake)
    async with open_checkpointer(url) as actual:
        assert actual is saver
        assert passed == [url]
        saver.setup.assert_awaited_once_with()
        assert not closed
    assert closed


@pytest.mark.parametrize("stage", ["connect", "setup", "close"])
def test_postgres_errors_do_not_expose_url(monkeypatch, caplog, stage):
    asyncio.run(_postgres_errors_do_not_expose_url(monkeypatch, caplog, stage))


async def _postgres_errors_do_not_expose_url(monkeypatch, caplog, stage):
    sentinel = "synthetic-password-sentinel"
    url = f"postgresql://synthetic:{sentinel}@127.0.0.1:5432/govops"
    saver = AsyncMock()
    if stage == "setup":
        saver.setup.side_effect = RuntimeError(url)

    @asynccontextmanager
    async def fake(connection_string):
        if stage == "connect":
            raise RuntimeError(connection_string)
        try:
            yield saver
        finally:
            if stage == "close":
                raise RuntimeError(connection_string)

    monkeypatch.setattr(AsyncPostgresSaver, "from_conn_string", fake)
    with pytest.raises(RuntimeError) as caught:
        async with open_checkpointer(url):
            pass
    for rendered in (str(caught.value), repr(caught.value), caplog.text):
        assert sentinel not in rendered
        assert url not in rendered
    assert "checkpointer DB" in str(caught.value)
