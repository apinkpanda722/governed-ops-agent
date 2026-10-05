"""Hash-only effect ledger with atomic key claims."""

import asyncio
import os
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from typing import Protocol

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


class OutboxState(StrEnum):
    RECORDED = "recorded"
    ATTEMPTED = "attempted"
    UNCERTAIN = "uncertain"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


OPEN = (OutboxState.RECORDED, OutboxState.ATTEMPTED, OutboxState.UNCERTAIN)


@dataclass(frozen=True)
class OutboxRow:
    idempotency_key: str
    task_id: str
    tool: str
    target: str
    args_sha256: str
    approval_id: str
    state: OutboxState
    attempts: int = 0
    mock_id: str | None = None
    audit_gap: bool = False
    reason: str | None = None


class OutboxStore(Protocol):
    async def claim(self, row: OutboxRow) -> tuple[OutboxRow, bool]: ...
    async def update(self, key: str, **changes) -> OutboxRow: ...
    async def get(self, key: str) -> OutboxRow | None: ...
    async def list_open(self) -> list[OutboxRow]: ...


MUTABLE = {"state", "attempts", "mock_id", "audit_gap", "reason"}


def _validate(changes):
    if not changes or changes.keys() - MUTABLE:
        raise ValueError("Invalid outbox changes")


class InMemoryOutboxStore:
    def __init__(self):
        self.rows: dict[str, OutboxRow] = {}
        self.lock = asyncio.Lock()

    async def claim(self, row):
        async with self.lock:
            if row.idempotency_key in self.rows:
                return self.rows[row.idempotency_key], False
            self.rows[row.idempotency_key] = row
            return row, True

    async def update(self, key, **changes):
        _validate(changes)
        async with self.lock:
            self.rows[key] = replace(self.rows[key], **changes)
            return self.rows[key]

    async def get(self, key):
        return self.rows.get(key)

    async def list_open(self):
        return [r for r in self.rows.values() if r.state in OPEN]


class PostgresOutboxStore:
    def __init__(self, url: str | None = None, *, table: str = "govops_outbox"):
        self._url = (
            url if url is not None else os.environ.get("GOVOPS_DATABASE_URL", "")
        )
        self.table = sql.Identifier(table)
        self._table_name = table
        self._ready = False
        self._setup_lock = asyncio.Lock()

    async def _run(self, operation, *args):
        try:
            async with await psycopg.AsyncConnection.connect(
                self._url, row_factory=dict_row
            ) as conn:
                async with self._setup_lock:
                    if not self._ready:
                        # Serialize first-time DDL across independent store instances.
                        await conn.execute(
                            "SELECT pg_advisory_xact_lock(hashtext(%s))",
                            (self._table_name,),
                        )
                        await conn.execute(
                            sql.SQL(
                                "CREATE TABLE IF NOT EXISTS {} (idempotency_key TEXT PRIMARY KEY, task_id TEXT NOT NULL, tool TEXT NOT NULL, target TEXT NOT NULL, args_sha256 TEXT NOT NULL, approval_id TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL, mock_id TEXT, audit_gap BOOLEAN NOT NULL, reason TEXT)"
                            ).format(self.table)
                        )
                        await conn.commit()
                        self._ready = True
                return await operation(conn, *args)
        except Exception:  # noqa: BLE001 — safe failure without transport details
            raise RuntimeError("outbox DB operation failed") from None

    @staticmethod
    def _row(record):
        return (
            None
            if record is None
            else OutboxRow(**{**record, "state": OutboxState(record["state"])})
        )

    async def _get(self, conn, key):
        cursor = await conn.execute(
            sql.SQL("SELECT * FROM {} WHERE idempotency_key = %s").format(self.table),
            (key,),
        )
        return self._row(await cursor.fetchone())

    async def claim(self, row):
        async def operation(conn):
            values = asdict(row)
            cursor = await conn.execute(
                sql.SQL(
                    "INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING RETURNING *"
                ).format(
                    self.table,
                    sql.SQL(",").join(map(sql.Identifier, values)),
                    sql.SQL(",").join(sql.Placeholder() for _ in values),
                ),
                tuple(values.values()),
            )
            inserted = await cursor.fetchone()
            return (
                (self._row(inserted), True)
                if inserted
                else (await self._get(conn, row.idempotency_key), False)
            )

        return await self._run(operation)

    async def get(self, key):
        return await self._run(self._get, key)

    async def update(self, key, **changes):
        _validate(changes)

        async def operation(conn):
            cursor = await conn.execute(
                sql.SQL(
                    "UPDATE {} SET {} WHERE idempotency_key = %s RETURNING *"
                ).format(
                    self.table,
                    sql.SQL(",").join(
                        sql.SQL("{} = %s").format(sql.Identifier(k)) for k in changes
                    ),
                ),
                (*changes.values(), key),
            )
            result = self._row(await cursor.fetchone())
            if result is None:
                raise KeyError("outbox row missing")
            return result

        return await self._run(operation)

    async def list_open(self):
        async def operation(conn):
            cursor = await conn.execute(
                sql.SQL(
                    "SELECT * FROM {} WHERE state = ANY(%s) ORDER BY idempotency_key"
                ).format(self.table),
                (list(OPEN),),
            )
            return [self._row(r) for r in await cursor.fetchall()]

        return await self._run(operation)
