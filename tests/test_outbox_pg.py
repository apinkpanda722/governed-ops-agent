import os
import uuid

import psycopg
import pytest
from psycopg import sql
from test_outbox import contract, sync

from govops.tools.outbox import PostgresOutboxStore


@sync
async def test_pg_contract():
    url = os.environ.get("GOVOPS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("test-only database URL required")
    table = "test_outbox_" + uuid.uuid4().hex
    store = PostgresOutboxStore(url, table=table)
    try:
        await contract(store)
    finally:
        try:
            async with await psycopg.AsyncConnection.connect(url) as conn:
                await conn.execute(
                    sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table))
                )
        except Exception:  # noqa: BLE001 — safe failure without transport details
            raise RuntimeError("outbox test cleanup failed") from None
