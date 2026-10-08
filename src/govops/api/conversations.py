"""Persistent turn-to-thread index, without request bodies or credentials."""

import asyncio
import os
from typing import Protocol

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


class ConversationError(Exception):
    def __init__(self, status_code: int, code: str):
        self.status_code = status_code
        self.code = code
        super().__init__(code)


class TurnIndex(Protocol):
    async def add(
        self, *, turn_id, conversation_id, owner_sub, turn_index, created_at
    ) -> None: ...
    async def list_for_owner(self, owner_sub: str, limit: int) -> list[dict]: ...
    async def conversation_of(self, turn_id) -> str | None: ...
    async def set_awaiting(self, turn_id, awaiting: bool) -> None: ...
    async def awaiting(self) -> list[tuple[str, str]]: ...
    async def delete_conversations(self, conversation_ids) -> int: ...


class InMemoryTurnIndex:
    def __init__(self):
        self.rows = {}

    async def add(self, *, turn_id, conversation_id, owner_sub, turn_index, created_at):
        self.rows.setdefault(
            turn_id,
            {
                "conversation_id": conversation_id,
                "owner_sub": owner_sub,
                "turn_index": turn_index,
                "created_at": created_at,
                "awaiting": False,
            },
        )

    async def list_for_owner(self, owner_sub: str, limit: int) -> list[dict]:
        groups = {}
        for row in self.rows.values():
            if row["owner_sub"] != owner_sub:
                continue
            ident = row["conversation_id"]
            group = groups.setdefault(
                ident,
                {
                    "conversation_id": ident,
                    "last_created_at": row["created_at"],
                    "turn_count": 0,
                },
            )
            group["last_created_at"] = max(group["last_created_at"], row["created_at"])
            group["turn_count"] += 1
        rows = sorted(groups.values(), key=lambda row: row["conversation_id"])
        rows.sort(key=lambda row: row["last_created_at"], reverse=True)
        return rows[:limit]

    async def conversation_of(self, turn_id):
        return self.rows.get(turn_id, {}).get("conversation_id")

    async def set_awaiting(self, turn_id, awaiting):
        if turn_id in self.rows:
            self.rows[turn_id]["awaiting"] = awaiting

    async def awaiting(self):
        return [
            (key, row["conversation_id"])
            for key, row in self.rows.items()
            if row["awaiting"]
        ]

    async def delete_conversations(self, conversation_ids):
        keys = {
            key
            for key, row in self.rows.items()
            if row["conversation_id"] in conversation_ids
        }
        for key in keys:
            del self.rows[key]
        return len(keys)


class PostgresTurnIndex:
    def __init__(self, url=None, *, table="govops_conversation_turns"):
        self._url = (
            url if url is not None else os.environ.get("GOVOPS_DATABASE_URL", "")
        )
        self.table = sql.Identifier(table)
        self._table_name = table
        self._ready = False
        self._setup_lock = asyncio.Lock()

    async def _run(self, operation):
        try:
            async with await psycopg.AsyncConnection.connect(
                self._url, row_factory=dict_row
            ) as conn:
                async with self._setup_lock:
                    if not self._ready:
                        await conn.execute(
                            "SELECT pg_advisory_xact_lock(hashtext(%s))",
                            (self._table_name,),
                        )
                        await conn.execute(
                            sql.SQL(
                                "CREATE TABLE IF NOT EXISTS {} (turn_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, owner_sub TEXT NOT NULL, turn_index INTEGER NOT NULL, created_at TEXT NOT NULL, awaiting BOOLEAN NOT NULL DEFAULT FALSE)"
                            ).format(self.table)
                        )
                        await conn.execute(
                            sql.SQL(
                                "CREATE INDEX IF NOT EXISTS {} ON {} (conversation_id)"
                            ).format(
                                sql.Identifier(
                                    self._table_name + "_conversation_id_idx"
                                ),
                                self.table,
                            )
                        )
                        await conn.commit()
                        self._ready = True
                return await operation(conn)
        except Exception:  # noqa: BLE001 — hide transport details
            raise RuntimeError("turn index DB operation failed") from None

    async def add(self, *, turn_id, conversation_id, owner_sub, turn_index, created_at):
        async def operation(conn):
            await conn.execute(
                sql.SQL(
                    "INSERT INTO {} (turn_id, conversation_id, owner_sub, turn_index, created_at) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING"
                ).format(self.table),
                (turn_id, conversation_id, owner_sub, turn_index, created_at),
            )

        await self._run(operation)

    async def list_for_owner(self, owner_sub: str, limit: int) -> list[dict]:
        async def operation(conn):
            cursor = await conn.execute(
                sql.SQL(
                    "SELECT conversation_id, max(created_at) AS last_created_at, count(*) AS turn_count FROM {} WHERE owner_sub = %s GROUP BY conversation_id ORDER BY last_created_at DESC, conversation_id ASC LIMIT %s"
                ).format(self.table),
                (owner_sub, limit),
            )
            return await cursor.fetchall()

        return await self._run(operation)

    async def conversation_of(self, turn_id):
        async def operation(conn):
            cursor = await conn.execute(
                sql.SQL("SELECT conversation_id FROM {} WHERE turn_id = %s").format(
                    self.table
                ),
                (turn_id,),
            )
            row = await cursor.fetchone()
            return row["conversation_id"] if row else None

        return await self._run(operation)

    async def set_awaiting(self, turn_id, awaiting):
        async def operation(conn):
            await conn.execute(
                sql.SQL("UPDATE {} SET awaiting = %s WHERE turn_id = %s").format(
                    self.table
                ),
                (awaiting, turn_id),
            )

        await self._run(operation)

    async def awaiting(self):
        async def operation(conn):
            cursor = await conn.execute(
                sql.SQL(
                    "SELECT turn_id, conversation_id FROM {} WHERE awaiting = TRUE"
                ).format(self.table)
            )
            return [
                (row["turn_id"], row["conversation_id"])
                for row in await cursor.fetchall()
            ]

        return await self._run(operation)

    async def delete_conversations(self, conversation_ids):
        async def operation(conn):
            cursor = await conn.execute(
                sql.SQL("DELETE FROM {} WHERE conversation_id = ANY(%s)").format(
                    self.table
                ),
                (list(conversation_ids),),
            )
            return cursor.rowcount

        return await self._run(operation)
