"""User-run checks confined to random tables and an isolated database."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import TypedDict
from uuid import uuid4

import psycopg
import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from psycopg import sql
from psycopg.conninfo import make_conninfo

from govops.agent.checkpointer import open_checkpointer
from govops.api.conversations import InMemoryTurnIndex, PostgresTurnIndex
from govops.api.retention import run_prune
from govops.audit.log import MemoryAuditLog


def database_url():
    url = os.environ.get("GOVOPS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("test-only database URL required")
    return make_conninfo(
        url,
        connect_timeout=5,
        options="-c statement_timeout=10000 -c lock_timeout=10000",
    )


async def bounded(operation):
    async with asyncio.timeout(30):
        return await operation


async def ddl(url, statement):
    async with asyncio.timeout(30):
        async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
            await conn.execute(statement)


def test_turn_index_postgres():
    url = database_url()

    async def exercise():
        table = "govops_m5_turns_" + uuid4().hex
        stores = [PostgresTurnIndex(url, table=table) for _ in range(2)]
        memory = InMemoryTurnIndex()
        rows = [
            {
                "turn_id": f"t{i}",
                "conversation_id": f"c{i}",
                "owner_sub": "synthetic-user",
                "turn_index": i,
                "created_at": "2026-10-08T00:00:00+00:00",
            }
            for i in range(2)
        ]
        try:
            await bounded(
                asyncio.gather(
                    *(store.add(**row) for store, row in zip(stores, rows, strict=True))
                )
            )
            for row in rows:
                await memory.add(**row)
            for store in stores:
                for row in rows:
                    await bounded(store.add(**{**row, "conversation_id": "ignored"}))
                    assert await bounded(
                        store.conversation_of(row["turn_id"])
                    ) == await memory.conversation_of(row["turn_id"])
                assert await bounded(store.conversation_of("missing")) is None
            for value in (True, False, True):
                await memory.set_awaiting("t0", value)
                await bounded(stores[0].set_awaiting("t0", value))
                for store in stores:
                    assert await bounded(store.awaiting()) == await memory.awaiting()
            await bounded(stores[0].set_awaiting("missing", True))
            for ids in ([], ["c0"], ["c0"], ["c1"]):
                assert await bounded(
                    stores[0].delete_conversations(ids)
                ) == await memory.delete_conversations(ids)
                assert await bounded(stores[1].awaiting()) == await memory.awaiting()
                for row in rows:
                    assert await bounded(
                        stores[1].conversation_of(row["turn_id"])
                    ) == await memory.conversation_of(row["turn_id"])
        finally:
            await ddl(
                url, sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table))
            )

    try:
        asyncio.run(exercise())
    except Exception:  # noqa: BLE001 — conceal transport details
        raise RuntimeError("M5 turn index verification failed") from None


class State(TypedDict):
    status: str
    expires_at: str


def test_retention_isolated_database():
    url = database_url()

    async def exercise():
        name = "govops_m5_test_" + uuid4().hex
        isolated = make_conninfo(url, dbname=name)
        created = False
        try:
            await ddl(url, sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            created = True
            async with asyncio.timeout(90):
                async with open_checkpointer(isolated) as saver:
                    builder = StateGraph(State)

                    def node(state):
                        if state["status"] == "awaiting_approval":
                            interrupt("synthetic-approval")
                        return state

                    builder.add_node("work", node)
                    builder.add_edge(START, "work")
                    builder.add_edge("work", END)
                    graph = builder.compile(checkpointer=saver)
                    now = datetime.now(UTC) + timedelta(days=8)
                    specs = {
                        "done": ("completed", now),
                        "expired": ("awaiting_approval", now - timedelta(days=1)),
                        "pending": ("awaiting_approval", now + timedelta(days=1)),
                        "running": ("running", now),
                    }
                    turns = InMemoryTurnIndex()
                    for thread, (status, expiry) in specs.items():
                        await graph.ainvoke(
                            {"status": status, "expires_at": expiry.isoformat()},
                            {"configurable": {"thread_id": thread}},
                        )
                        await turns.add(
                            turn_id=thread,
                            conversation_id=thread,
                            owner_sub="synthetic-user",
                            turn_index=1,
                            created_at=now.isoformat(),
                        )
                    audit = MemoryAuditLog()
                    dry = await run_prune(saver, turns, audit, now=now, apply=False)
                    assert set(dry.candidates) == {"done", "expired"}
                    assert not audit.records
                    result = await run_prune(saver, turns, audit, now=now, apply=True)
                    assert result.deleted == 2
                    assert set(result.deleted_ids) == {"done", "expired"}
                    for thread in specs:
                        retained = thread in {"pending", "running"}
                        config = {"configurable": {"thread_id": thread}}
                        assert bool(await saver.aget_tuple(config)) == retained
                        assert (
                            bool([item async for item in saver.alist(config)])
                            == retained
                        )
                        assert bool(await turns.conversation_of(thread)) == retained
                    assert audit.records == [
                        {
                            "event": "checkpoint.pruned",
                            "task_id": None,
                            "actor_sub": "system",
                            "pruned_count": 2,
                            "cutoff": (now - timedelta(days=7)).isoformat(),
                        }
                    ]
        finally:
            if created:
                await ddl(
                    url,
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(name)
                    ),
                )

    try:
        asyncio.run(exercise())
    except Exception:  # noqa: BLE001 — conceal transport details
        raise RuntimeError("M5 isolated retention verification failed") from None
