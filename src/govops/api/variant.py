"""Immutable conversation assignments, retained until thread deletion."""

import asyncio
import os
import re
from collections.abc import Collection
from typing import Protocol

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


class VariantStore(Protocol):
    async def put(
        self,
        *,
        conversation_id: str,
        variant: str,
        bucket: int,
        pct_bp: int,
        assigned_at: str,
    ) -> bool: ...
    async def get_many(self, conversation_ids: Collection[str]) -> dict[str, str]: ...
    async def recent(self, variant: str, limit: int) -> list[str]: ...
    async def delete(self, conversation_ids: Collection[str]) -> int: ...


def _validate(conversation_id, variant, bucket, pct_bp):
    if (
        not isinstance(conversation_id, str)
        or re.fullmatch(r"[0-9a-f]{32}", conversation_id) is None
    ):
        raise ValueError("invalid_conversation_id")
    if variant not in ("v1", "v2"):
        raise ValueError("invalid_variant")
    if type(bucket) is not int or not 0 <= bucket <= 9999:
        raise ValueError("invalid_bucket")
    if type(pct_bp) is not int or not 0 <= pct_bp <= 10000:
        raise ValueError("invalid_pct_bp")


class InMemoryVariantStore:
    def __init__(self):
        self._rows = {}

    async def put(
        self,
        *,
        conversation_id: str,
        variant: str,
        bucket: int,
        pct_bp: int,
        assigned_at: str,
    ) -> bool:
        _validate(conversation_id, variant, bucket, pct_bp)
        # No await between checking and inserting: one winner per event loop.
        if conversation_id in self._rows:
            return False
        self._rows[conversation_id] = {
            "variant": variant,
            "bucket": bucket,
            "pct_bp": pct_bp,
            "assigned_at": assigned_at,
        }
        return True

    async def get_many(self, conversation_ids: Collection[str]) -> dict[str, str]:
        return {
            ident: self._rows[ident]["variant"]
            for ident in conversation_ids
            if ident in self._rows
        }

    async def recent(self, variant: str, limit: int) -> list[str]:
        ids = sorted(
            ident for ident, row in self._rows.items() if row["variant"] == variant
        )
        ids.sort(key=lambda ident: self._rows[ident]["assigned_at"], reverse=True)
        return ids[:limit]

    async def delete(self, conversation_ids: Collection[str]) -> int:
        ids = set(conversation_ids) & self._rows.keys()
        for ident in ids:
            del self._rows[ident]
        return len(ids)


class PostgresVariantStore:
    def __init__(self, url=None, *, table="govops_conversation_variant"):
        self._url = (
            url if url is not None else os.environ.get("GOVOPS_DATABASE_URL", "")
        )
        self.table = sql.Identifier(table)
        self._table_name = table
        self._ready = False
        self._setup_lock = asyncio.Lock()

    async def _run(self, operation, *, prepare=True):
        try:
            async with await psycopg.AsyncConnection.connect(
                self._url, row_factory=dict_row
            ) as conn:
                if prepare:
                    async with self._setup_lock:
                        if not self._ready:
                            await conn.execute(
                                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                                (self._table_name,),
                            )
                            await conn.execute(
                                sql.SQL(
                                    "CREATE TABLE IF NOT EXISTS {} (conversation_id TEXT PRIMARY KEY, variant TEXT NOT NULL, bucket INTEGER NOT NULL, pct_bp INTEGER NOT NULL, assigned_at TEXT NOT NULL)"
                                ).format(self.table)
                            )
                            await conn.execute(
                                sql.SQL(
                                    "CREATE INDEX IF NOT EXISTS {} ON {} (variant, assigned_at)"
                                ).format(
                                    sql.Identifier(
                                        self._table_name + "_variant_assigned_at_idx"
                                    ),
                                    self.table,
                                )
                            )
                            await conn.commit()
                            self._ready = True
                return await operation(conn)
        except Exception:  # noqa: BLE001 — hide transport details
            raise RuntimeError("variant store DB operation failed") from None

    async def put(
        self,
        *,
        conversation_id: str,
        variant: str,
        bucket: int,
        pct_bp: int,
        assigned_at: str,
    ) -> bool:
        _validate(conversation_id, variant, bucket, pct_bp)

        async def operation(conn):
            cursor = await conn.execute(
                sql.SQL(
                    "INSERT INTO {} (conversation_id, variant, bucket, pct_bp, assigned_at) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING"
                ).format(self.table),
                (conversation_id, variant, bucket, pct_bp, assigned_at),
            )
            return cursor.rowcount == 1

        return await self._run(operation)

    async def get_many(self, conversation_ids: Collection[str]) -> dict[str, str]:
        async def operation(conn):
            cursor = await conn.execute(
                sql.SQL(
                    "SELECT conversation_id, variant FROM {} WHERE conversation_id = ANY(%s)"
                ).format(self.table),
                (list(conversation_ids),),
            )
            return {
                row["conversation_id"]: row["variant"]
                for row in await cursor.fetchall()
            }

        return await self._run(operation)

    async def recent(self, variant: str, limit: int) -> list[str]:
        async def operation(conn):
            cursor = await conn.execute(
                sql.SQL(
                    "SELECT conversation_id FROM {} WHERE variant = %s ORDER BY assigned_at DESC, conversation_id ASC LIMIT %s"
                ).format(self.table),
                (variant, limit),
            )
            return [row["conversation_id"] for row in await cursor.fetchall()]

        return await self._run(operation)

    async def delete(self, conversation_ids: Collection[str]) -> int:
        async def operation(conn):
            cursor = await conn.execute(
                "SELECT to_regclass(%s) IS NOT NULL AS present",
                (self.table.as_string(),),
            )
            if not (await cursor.fetchone())["present"]:
                return 0
            cursor = await conn.execute(
                sql.SQL("DELETE FROM {} WHERE conversation_id = ANY(%s)").format(
                    self.table
                ),
                (list(conversation_ids),),
            )
            return cursor.rowcount

        return await self._run(operation, prepare=False)
