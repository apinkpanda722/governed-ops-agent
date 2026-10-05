import asyncio
from functools import wraps

import pytest

from govops.tools.outbox import InMemoryOutboxStore, OutboxRow, OutboxState


def sync(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapped


async def contract(store):
    row = OutboxRow(
        "a" * 64,
        "syn-task",
        "slack_send_message",
        "announce",
        "b" * 64,
        "syn-approval",
        OutboxState.RECORDED,
        0,
        None,
        False,
        None,
    )
    claims = await asyncio.gather(*(store.claim(row) for _ in range(5)))
    assert sum(new for _, new in claims) == 1
    assert await store.get(row.idempotency_key) == row
    assert await store.list_open() == [row]
    changed = await store.update(
        row.idempotency_key,
        state=OutboxState.SUCCEEDED,
        attempts=1,
        mock_id="message-1",
    )
    assert changed.mock_id == "message-1"
    assert await store.list_open() == []
    assert not (await store.claim(row))[1]
    with pytest.raises((ValueError, TypeError)):
        await store.update(row.idempotency_key, body="secret")


@sync
async def test_memory_contract():
    await contract(InMemoryOutboxStore())


@sync
async def test_all_open_states_and_hash_only_rows():
    from dataclasses import fields

    store = InMemoryOutboxStore()
    for n, state in enumerate(OutboxState):
        await store.claim(
            OutboxRow(
                str(n),
                "syn-task",
                "slack_send_message",
                "announce",
                "a" * 64,
                "syn-approval",
                state,
            )
        )
    assert {r.state for r in await store.list_open()} == {
        OutboxState.RECORDED,
        OutboxState.ATTEMPTED,
        OutboxState.UNCERTAIN,
    }
    assert {"body", "args", "text", "user_id"}.isdisjoint(
        f.name for f in fields(OutboxRow)
    )


@sync
async def test_postgres_errors_do_not_expose_connection_details(monkeypatch):
    from govops.tools.outbox import PostgresOutboxStore

    async def fail(*args, **kwargs):
        raise ValueError("synthetic-private-connection")

    monkeypatch.setattr("govops.tools.outbox.psycopg.AsyncConnection.connect", fail)
    with pytest.raises(RuntimeError, match="^outbox DB operation failed$") as caught:
        await PostgresOutboxStore("synthetic-private-connection").get("a" * 64)
    assert caught.value.__suppress_context__ is True


@sync
async def test_open_rows_include_interrupted_attempts_after_update():
    store = InMemoryOutboxStore()
    row = OutboxRow(
        "a" * 64,
        "syn-task",
        "slack_send_message",
        "announce",
        "b" * 64,
        "syn-approval",
        OutboxState.RECORDED,
    )
    await store.claim(row)
    for state in (OutboxState.ATTEMPTED, OutboxState.UNCERTAIN):
        await store.update(row.idempotency_key, state=state)
        assert (await store.list_open())[0].state == state
