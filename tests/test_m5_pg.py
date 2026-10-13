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
from psycopg.rows import dict_row
from test_agent_graph import FakeTools, reply

from govops.agent.checkpointer import open_checkpointer
from govops.agent.graph import build_graph
from govops.api.conversations import InMemoryTurnIndex, PostgresTurnIndex
from govops.api.retention import run_prune, startup_prune
from govops.api.tasks import TaskService
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
            for store in stores:
                for limit in (1, 50):
                    actual = await bounded(
                        store.list_for_owner("synthetic-user", limit)
                    )
                    assert actual == await memory.list_for_owner(
                        "synthetic-user", limit
                    )
                    assert len(actual) == min(limit, 2)
                assert await bounded(store.list_for_owner("other-user", 50)) == []
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


@pytest.mark.parametrize("concurrent", [False, True])
def test_turn_index_upgrades_old_schema_postgres(concurrent):
    """Existing rows survive ALTER, including simultaneous first opens."""
    url = database_url()

    async def exercise():
        table = "govops_m5_old_turns_" + uuid4().hex
        identifier = sql.Identifier(table)
        stores = [PostgresTurnIndex(url, table=table) for _ in range(2)]
        try:
            await ddl(
                url,
                sql.SQL(
                    "CREATE TABLE {} (turn_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, owner_sub TEXT NOT NULL, turn_index INTEGER NOT NULL, created_at TEXT NOT NULL, awaiting BOOLEAN NOT NULL DEFAULT FALSE)"
                ).format(identifier),
            )
            async with await psycopg.AsyncConnection.connect(url) as conn:
                await conn.execute(
                    sql.SQL("INSERT INTO {} VALUES (%s, %s, %s, %s, %s, %s)").format(
                        identifier
                    ),
                    ("old-turn", "old", "synthetic-user", 0, "old-time", True),
                )
                cursor = await conn.execute(
                    sql.SQL("SELECT * FROM {} LIMIT 0").format(identifier)
                )
                assert "deleted_at" not in {
                    column.name for column in cursor.description
                }
            expected = [
                {
                    "conversation_id": "old",
                    "last_created_at": "old-time",
                    "turn_count": 1,
                }
            ]
            if concurrent:
                results = await bounded(
                    asyncio.gather(
                        *(
                            store.list_for_owner("synthetic-user", 50)
                            for store in stores
                        )
                    )
                )
                assert results == [expected, expected]
            else:
                for store in stores:
                    assert (
                        await bounded(store.list_for_owner("synthetic-user", 50))
                        == expected
                    )
            async with await psycopg.AsyncConnection.connect(
                url, row_factory=dict_row
            ) as conn:
                cursor = await conn.execute(
                    sql.SQL("SELECT * FROM {} WHERE turn_id = %s").format(identifier),
                    ("old-turn",),
                )
                assert await cursor.fetchone() == {
                    "turn_id": "old-turn",
                    "conversation_id": "old",
                    "owner_sub": "synthetic-user",
                    "turn_index": 0,
                    "created_at": "old-time",
                    "awaiting": True,
                    "deleted_at": None,
                }
            for store in stores:
                assert await bounded(store.conversation_of("old-turn")) == "old"
                assert await bounded(store.awaiting()) == [("old-turn", "old")]
                assert await bounded(store.is_deleted("old")) is False
                assert await bounded(store.deleted_conversation_ids()) == []
            await bounded(stores[0].set_awaiting("old-turn", False))
            assert await bounded(stores[1].awaiting()) == []
            await bounded(
                stores[0].add(
                    turn_id="new-turn",
                    conversation_id="old",
                    owner_sub="synthetic-user",
                    turn_index=1,
                    created_at="new-time",
                )
            )
            assert await bounded(stores[1].conversation_of("new-turn")) == "old"
            assert await bounded(stores[1].delete_conversations(["old"])) == 2
            assert await bounded(stores[0].conversation_of("old-turn")) is None
        finally:
            await ddl(url, sql.SQL("DROP TABLE IF EXISTS {}").format(identifier))

    try:
        asyncio.run(exercise())
    except Exception:  # noqa: BLE001 — conceal transport details
        raise RuntimeError("M5 old-schema turn index verification failed") from None


def test_turn_index_soft_delete_postgres():
    url = database_url()

    async def exercise():
        table = "govops_m5_deleted_turns_" + uuid4().hex
        stores = [PostgresTurnIndex(url, table=table) for _ in range(2)]
        memory = InMemoryTurnIndex()
        rows = [
            {
                "turn_id": f"t{i}",
                "conversation_id": conversation,
                "owner_sub": owner,
                "turn_index": i,
                "created_at": f"2026-10-13T00:00:0{i}+00:00",
            }
            for i, (conversation, owner) in enumerate(
                [("z", "a"), ("z", "a"), ("z", "a"), ("a", "a"), ("b", "b")]
            )
        ]

        async def equivalent():
            for store in stores:
                for owner in ("a", "b", "missing"):
                    for limit in (1, 50):
                        assert await bounded(
                            store.list_for_owner(owner, limit)
                        ) == await memory.list_for_owner(owner, limit)
                assert set(await bounded(store.awaiting())) == set(
                    await memory.awaiting()
                )
                assert (
                    await bounded(store.deleted_conversation_ids())
                    == await memory.deleted_conversation_ids()
                )
                for conversation in ("z", "a", "b", "missing"):
                    assert await bounded(
                        store.is_deleted(conversation)
                    ) == await memory.is_deleted(conversation)
                for row in rows:
                    assert await bounded(
                        store.conversation_of(row["turn_id"])
                    ) == await memory.conversation_of(row["turn_id"])
            async with await psycopg.AsyncConnection.connect(
                url, row_factory=dict_row
            ) as conn:
                cursor = await conn.execute(
                    sql.SQL("SELECT turn_id, deleted_at FROM {}").format(
                        sql.Identifier(table)
                    )
                )
                assert {
                    row["turn_id"]: row["deleted_at"] for row in await cursor.fetchall()
                } == {turn: row["deleted_at"] for turn, row in memory.rows.items()}

        try:
            for row in rows:
                for store in (memory, stores[0]):
                    await bounded(store.add(**row))
                    await bounded(store.set_awaiting(row["turn_id"], True))
            await equivalent()
            for conversation, owner, timestamp, count in (
                ("z", "b", "2026-10-13T00:00:00+00:00", 0),
                ("missing", "a", "2026-10-13T00:00:00+00:00", 0),
                ("z", "a", "2026-10-13T00:00:00+00:00", 3),
                ("z", "a", "2026-10-14T00:00:00+00:00", 3),
                ("b", "b", "2026-10-13T00:00:00+00:00", 1),
                ("a", "a", "2026-10-13T00:00:00+00:00", 1),
            ):
                assert (
                    await memory.mark_deleted(conversation, owner, timestamp) == count
                )
                assert (
                    await bounded(
                        stores[0].mark_deleted(conversation, owner, timestamp)
                    )
                    == count
                )
                await equivalent()
            assert await memory.deleted_conversation_ids() == ["a", "b", "z"]
            for ids, count in (([], 0), (["z"], 3), (["z"], 0), (["a", "b"], 2)):
                assert await memory.delete_conversations(ids) == count
                assert await bounded(stores[0].delete_conversations(ids)) == count
                await equivalent()
            assert await bounded(stores[1].is_deleted("z")) is False
        finally:
            await ddl(
                url, sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table))
            )

    try:
        asyncio.run(exercise())
    except Exception:  # noqa: BLE001 — conceal transport details
        raise RuntimeError("M5 soft-delete turn index verification failed") from None


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


STARTUP_BOUND = 20  # 초. 교착하면 끝나지 않는다. 정상 경로는 1초 안이다.


def test_startup_with_existing_threads_finishes_in_isolated_database():
    """thread가 있는 실제 Postgres에서 serve 시작 경로(startup_cleanup → startup_prune)가 끝난다.

    AsyncPostgresSaver.alist는 잠금을 쥔 채 yield한다. 순회 안에서 같은 saver를 부르면 교착한다
    (InMemorySaver에는 이 잠금이 없어 단위 테스트가 잡지 못했다). open_task_service 전체는 live A·LiteLLM·팀 키가
    필요해 여기서 부르지 못하므로, 같은 saver 위에서 시작 때 실제로 실행되는 두 함수를 검증한다.
    """
    url = database_url()

    class Chat:
        async def complete(self, **kwargs):
            return reply("합성 답변")

    async def exercise():
        name = "govops_m5_test_" + uuid4().hex
        isolated = make_conninfo(url, dbname=name)
        created = False
        try:
            await ddl(url, sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            created = True
            async with asyncio.timeout(90):
                async with open_checkpointer(isolated) as saver:
                    graph = build_graph(Chat(), FakeTools(), saver)
                    first = TaskService(
                        graph, turns=InMemoryTurnIndex(), audit=MemoryAuditLog()
                    )
                    ids = [await first.start(user, "합성 요청") for user in "abc"]
                    await asyncio.gather(*first._tasks)
                    await graph.aupdate_state(
                        {"configurable": {"thread_id": ids[-1]}},
                        {"status": "running"},
                        as_node="model",
                    )
                    await first.aclose()
                    # 새 서비스 = 재시작. 시작 때 부르는 두 함수가 thread가 있는 DB에서 끝나야 한다.
                    audit = MemoryAuditLog()
                    service = TaskService(graph, turns=InMemoryTurnIndex(), audit=audit)
                    await asyncio.wait_for(service.startup_cleanup(), STARTUP_BOUND)
                    await asyncio.wait_for(
                        startup_prune(service, saver, service.turns), STARTUP_BOUND
                    )
                    assert (await service.get("a", ids[0]))["status"] == "completed"
                    assert (await service.get("c", ids[-1]))["status"] == "interrupted"
                    assert not audit.records  # 7일 이내라 삭제 대상 없음
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
        raise RuntimeError("M5 startup verification failed") from None
