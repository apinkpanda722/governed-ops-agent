"""Offline contracts for immutable conversation assignments."""

import asyncio
import random
import socket

import pytest
from test_agent_graph import async_test

from govops.api.variant import InMemoryVariantStore, PostgresVariantStore


def assignment(conversation_id="a" * 32, **changes):
    return {
        "conversation_id": conversation_id,
        "variant": "v1",
        "bucket": 0,
        "pct_bp": 10000,
        "assigned_at": "2026-10-21T00:00:00+00:00",
        **changes,
    }


@async_test
async def test_first_assignment_is_immutable_and_get_many_omits_missing():
    store = InMemoryVariantStore()
    assert await store.put(**assignment()) is True
    assert await store.put(**assignment(variant="v2", bucket=9999, pct_bp=0)) is False
    assert await store.get_many(["a" * 32, "b" * 32]) == {"a" * 32: "v1"}
    assert await store.recent("v1", 10) == ["a" * 32]
    assert await store.recent("v2", 10) == []
    assert await store.get_many([]) == {}


@pytest.mark.parametrize("factory", [InMemoryVariantStore, PostgresVariantStore])
@pytest.mark.parametrize(
    "changes",
    [
        {"conversation_id": "a" * 31},
        {"conversation_id": "a" * 33},
        {"conversation_id": "A" * 32},
        {"conversation_id": "g" * 32},
        {"conversation_id": "a" * 32 + "\n"},
        {"conversation_id": None},
        {"variant": "v3"},
        {"variant": None},
        {"bucket": -1},
        {"bucket": 10000},
        {"bucket": 1.0},
        {"bucket": True},
        {"pct_bp": -1},
        {"pct_bp": 10001},
        {"pct_bp": "10"},
        {"pct_bp": False},
    ],
)
@async_test
async def test_invalid_put_is_rejected_before_connecting(factory, changes):
    with pytest.raises(ValueError):
        await factory().put(**assignment(**changes))


@async_test
async def test_recent_orders_by_time_then_id_and_separates_variants():
    store = InMemoryVariantStore()
    for ident, variant, time in (
        ("d", "v1", "2026-10-20T00:00:00+00:00"),
        ("c", "v1", "2026-10-21T00:00:00+00:00"),
        ("b", "v1", "2026-10-21T00:00:00+00:00"),
        ("a", "v2", "2026-10-22T00:00:00+00:00"),
    ):
        assert await store.put(
            **assignment(ident * 32, variant=variant, assigned_at=time)
        )
    assert await store.recent("v1", 10) == [c * 32 for c in ("b", "c", "d")]
    assert await store.recent("v1", 2) == [c * 32 for c in ("b", "c")]
    assert await store.recent("v1", 0) == []
    assert await store.recent("v2", 10) == ["a" * 32]


@async_test
async def test_delete_counts_rows_once_and_accepts_unformatted_thread_ids():
    store = InMemoryVariantStore()
    for ident in ("a", "b"):
        await store.put(**assignment(ident * 32))
    assert await store.delete([]) == 0
    assert await store.delete(["legacy-thread", "a" * 32, "a" * 32]) == 1
    assert await store.delete(["a" * 32]) == 0
    assert await store.get_many(["a" * 32, "b" * 32]) == {"b" * 32: "v1"}
    assert await store.delete(["b" * 32]) == 1


@async_test
async def test_concurrent_put_has_exactly_one_winner():
    store = InMemoryVariantStore()
    results = await asyncio.gather(
        *(store.put(**assignment(variant=v)) for v in ("v1", "v2"))
    )
    assert set(results) == {True, False}
    assert sum(results) == 1
    saved = await store.get_many(["a" * 32])
    assert len(saved) == 1 and set(saved.values()) <= {"v1", "v2"}
    assert saved == {"a" * 32: next(v for v, won in zip(("v1", "v2"), results) if won)}


@async_test
async def test_seeded_hex_identifiers_and_numeric_boundaries():
    store = InMemoryVariantStore()
    rng = random.Random(10)
    for _ in range(10000):
        assert await store.put(**assignment(f"{rng.getrandbits(128):032x}"))
    assert await store.put(**assignment("f" * 32, bucket=9999, pct_bp=0))


@pytest.mark.timeout(10)
@pytest.mark.parametrize("operation", ["put", "get_many", "recent", "delete"])
@async_test
async def test_postgres_connection_failure_has_only_safe_message(operation):
    # A bound, non-listening loopback socket reserves a closed port for this test.
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        store = PostgresVariantStore(
            f"host=127.0.0.1 port={port} dbname=synthetic connect_timeout=1"
        )
        calls = {
            "put": lambda: store.put(**assignment()),
            "get_many": lambda: store.get_many(["a" * 32]),
            "recent": lambda: store.recent("v1", 1),
            "delete": lambda: store.delete(["legacy-thread"]),
        }
        with pytest.raises(RuntimeError) as caught:
            await asyncio.wait_for(calls[operation](), timeout=5)
    assert str(caught.value) == "variant store DB operation failed"
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
