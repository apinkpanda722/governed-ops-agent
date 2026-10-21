"""Offline retention orchestration and sanitized CLI contracts."""

import asyncio
import copy
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import async_test
from test_agent_retention import ANSWER, DRAFT, MESSAGE, saved_thread

from govops.api import retention
from govops.api.conversations import InMemoryTurnIndex
from govops.api.variant import InMemoryVariantStore
from govops.audit.log import AuditWriteError, MemoryAuditLog


async def fixture_threads():
    saver, turns, audit = InMemorySaver(), InMemoryTurnIndex(), MemoryAuditLog()
    base = datetime.now(UTC)
    for name, status, expiry in (
        ("old", "completed", None),
        ("running", "running", None),
        ("waiting", "awaiting_approval", base + timedelta(days=10)),
        ("expired", "awaiting_approval", base + timedelta(days=1)),
    ):
        _, ts = await saved_thread(
            saver, name, status, expiry.isoformat() if expiry else None
        )
        for n in (1, 2):
            await turns.add(
                turn_id=f"{name}-turn-{n}",
                conversation_id=name,
                owner_sub="synthetic-owner",
                turn_index=n,
                created_at=ts.isoformat(),
            )
    now = ts + timedelta(days=8)
    # This checkpoint is less than seven days old at the injected prune time.
    from langgraph.checkpoint.base import empty_checkpoint

    checkpoint = empty_checkpoint()
    checkpoint["ts"] = (now - timedelta(hours=1)).isoformat()
    checkpoint["channel_values"] = {"status": "completed", "answer": ANSWER}
    checkpoint["channel_versions"] = {"status": 1, "answer": 1}
    await saver.aput(
        {"configurable": {"thread_id": "recent", "checkpoint_ns": ""}},
        checkpoint,
        {"source": "update", "step": 0, "parents": {}},
        checkpoint["channel_versions"],
    )
    await turns.add(
        turn_id="recent-turn",
        conversation_id="recent",
        owner_sub="synthetic-owner",
        turn_index=1,
        created_at=checkpoint["ts"],
    )
    return saver, turns, audit, now


@async_test
async def test_dry_run_has_no_side_effects():
    saver, turns, audit, now = await fixture_threads()
    before = copy.deepcopy(turns.rows)
    saver.adelete_thread = AsyncMock(wraps=saver.adelete_thread)
    result = await retention.run_prune(saver, turns, audit, now=now, apply=False)
    assert result.candidates == ("expired", "old")
    assert result.deleted == 0
    assert audit.records == []
    assert turns.rows == before
    saver.adelete_thread.assert_not_called()


@async_test
async def test_apply_audits_intent_before_deleting_and_cleans_index():
    saver, turns, audit, now = await fixture_threads()
    delete = saver.adelete_thread

    async def audited_delete(thread_id):
        assert audit.records == [
            {
                "event": "checkpoint.pruned",
                "task_id": None,
                "actor_sub": "system",
                "pruned_count": 2,
                "cutoff": (now - timedelta(days=7)).isoformat(),
            }
        ]
        await delete(thread_id)

    saver.adelete_thread = AsyncMock(side_effect=audited_delete)
    result = await retention.run_prune(saver, turns, audit, now=now, apply=True)
    assert result.deleted_ids == ("expired", "old")
    assert result.deleted == 2 and result.active == 2 and result.scanned == 5
    assert {row["conversation_id"] for row in turns.rows.values()} == {
        "running",
        "waiting",
        "recent",
    }
    for name in ("old", "expired", "running", "waiting", "recent"):
        saved = await saver.aget_tuple({"configurable": {"thread_id": name}})
        assert (saved is None) == (name in result.deleted_ids)
    assert len(audit.records) == 1
    record = audit.records[0]
    assert set(record) - {"event", "task_id", "actor_sub"} == {"pruned_count", "cutoff"}
    assert all(
        raw not in repr(record) for raw in (ANSWER, MESSAGE, DRAFT, "old", "expired")
    )


@pytest.mark.parametrize("apply", [False, True])
@async_test
async def test_deleted_recent_conversation_prunes_all_turns(monkeypatch, apply):
    saver, turns, audit, now = await fixture_threads()
    await turns.add(
        turn_id="recent-turn-2",
        conversation_id="recent",
        owner_sub="synthetic-owner",
        turn_index=2,
        created_at=now.isoformat(),
    )
    for name, count in (("recent", 2), ("running", 2), ("waiting", 2), ("expired", 2)):
        assert (
            await turns.mark_deleted(name, "synthetic-owner", now.isoformat()) == count
        )
    before = copy.deepcopy(turns.rows)
    turns.deleted_conversation_ids = AsyncMock(wraps=turns.deleted_conversation_ids)
    prune = AsyncMock(wraps=retention.prune_checkpoints)
    monkeypatch.setattr(retention, "prune_checkpoints", prune)
    delete = saver.adelete_thread

    async def audited_delete(thread_id):
        assert audit.records == [
            {
                "event": "checkpoint.pruned",
                "task_id": None,
                "actor_sub": "system",
                "pruned_count": 3,
                "cutoff": (now - timedelta(days=7)).isoformat(),
            }
        ]
        await delete(thread_id)

    saver.adelete_thread = AsyncMock(side_effect=audited_delete)
    result = await retention.run_prune(saver, turns, audit, now=now, apply=apply)
    assert result.candidates == ("expired", "old", "recent")
    assert result.scanned == 5 and result.active == 2
    turns.deleted_conversation_ids.assert_awaited_once_with()
    assert [call.kwargs["apply"] for call in prune.await_args_list] == (
        [False, True] if apply else [False]
    )
    deleted_ids = prune.await_args_list[0].kwargs["deleted_conversation_ids"]
    assert deleted_ids == ["expired", "recent", "running", "waiting"]
    assert all(
        call.kwargs["deleted_conversation_ids"] is deleted_ids
        for call in prune.await_args_list
    )
    if apply:
        assert result.deleted_ids == result.candidates and result.deleted == 3
        assert turns.rows == {
            key: row
            for key, row in before.items()
            if row["conversation_id"] in {"running", "waiting"}
        }
        assert len(audit.records) == 1
    else:
        assert result.deleted == 0 and result.deleted_ids == ()
        assert turns.rows == before
        assert audit.records == []
        saver.adelete_thread.assert_not_called()
    for name in ("old", "expired", "recent", "running", "waiting"):
        saved = await saver.aget_tuple({"configurable": {"thread_id": name}})
        assert (saved is None) == (apply and name in result.candidates)


@pytest.mark.parametrize("apply", [False, True])
@async_test
async def test_deleted_ids_read_failure_prevents_pruning(monkeypatch, apply):
    saver, turns, audit, now = await fixture_threads()
    before = copy.deepcopy(turns.rows)
    turns.deleted_conversation_ids = AsyncMock(
        side_effect=RuntimeError("private detail")
    )
    prune = AsyncMock(wraps=retention.prune_checkpoints)
    monkeypatch.setattr(retention, "prune_checkpoints", prune)
    saver.adelete_thread = AsyncMock(wraps=saver.adelete_thread)
    with pytest.raises(RuntimeError, match="private detail"):
        await retention.run_prune(saver, turns, audit, now=now, apply=apply)
    turns.deleted_conversation_ids.assert_awaited_once_with()
    prune.assert_not_called()
    saver.adelete_thread.assert_not_called()
    assert audit.records == [] and turns.rows == before


@pytest.mark.parametrize("missing", [False, True])
@async_test
async def test_missing_or_failed_audit_prevents_deletion(missing):
    saver, turns, audit, now = await fixture_threads()
    before = copy.deepcopy(turns.rows)
    saver.adelete_thread = AsyncMock(wraps=saver.adelete_thread)
    if missing:
        result = await retention.run_prune(saver, turns, None, now=now, apply=True)
        assert result.deleted == 0
    else:

        def fail(*args, **kwargs):
            raise AuditWriteError("synthetic private detail")

        audit.append = fail
        with pytest.raises(AuditWriteError):
            await retention.run_prune(saver, turns, audit, now=now, apply=True)
    assert audit.records == []
    assert turns.rows == before
    saver.adelete_thread.assert_not_called()


@async_test
async def test_no_candidates_does_not_audit():
    audit = MemoryAuditLog()
    result = await retention.run_prune(
        InMemorySaver(), InMemoryTurnIndex(), audit, now=datetime.now(UTC), apply=True
    )
    assert result.candidates == () and result.deleted == 0
    assert audit.records == []


@async_test
async def test_partial_delete_only_cleans_deleted_index():
    saver, turns, audit, now = await fixture_threads()
    delete = saver.adelete_thread

    async def fail_one(thread_id):
        if thread_id == "expired":
            raise RuntimeError("synthetic private detail")
        await delete(thread_id)

    saver.adelete_thread = fail_one
    result = await retention.run_prune(saver, turns, audit, now=now, apply=True)
    assert result.deleted_ids == ("old",) and result.deleted == 1
    assert audit.records[0]["pruned_count"] == 2
    assert await turns.conversation_of("old-turn-1") is None
    assert await turns.conversation_of("expired-turn-1") == "expired"
    assert await saver.aget_tuple({"configurable": {"thread_id": "expired"}})


@async_test
async def test_startup_exception_is_one_safe_warning(monkeypatch, capsys):
    prune = AsyncMock(side_effect=RuntimeError("private thread path detail"))
    monkeypatch.setattr(retention, "run_prune", prune)
    service, saver, turns = SimpleNamespace(audit=MemoryAuditLog()), object(), object()
    await retention.startup_prune(service, saver, turns)
    args, kwargs = prune.call_args
    assert args == (saver, turns)
    assert kwargs["audit"] is service.audit and kwargs["apply"] is True
    assert kwargs["now"].utcoffset() == timedelta(0)
    output = capsys.readouterr()
    assert output.out == "" and output.err == "WARN checkpoint_prune_failed\n"


@pytest.mark.parametrize("apply", [False, True])
def test_cli_uses_fake_dependencies_and_safe_summary(monkeypatch, capsys, apply):
    saver, turns, audit, now = asyncio.run(fixture_threads())
    before = copy.deepcopy(turns.rows)
    calls = []
    monkeypatch.setattr(retention, "datetime", SimpleNamespace(now=lambda tz: now))
    monkeypatch.setenv("GOVOPS_DATABASE_URL", "synthetic-db")
    monkeypatch.delenv("AUDIT_KEY_FILE", raising=False)
    monkeypatch.setenv("AUDIT_DIR", "synthetic-audit-dir")

    @asynccontextmanager
    async def opened(url):
        assert url == "synthetic-db"
        yield saver

    def audit_factory(directory):
        assert str(directory) == "synthetic-audit-dir"
        calls.append("audit")
        return audit

    monkeypatch.setattr(retention, "open_checkpointer", opened)
    monkeypatch.setattr(retention, "PostgresTurnIndex", lambda: turns)
    monkeypatch.setattr(
        retention, "PostgresVariantStore", lambda: InMemoryVariantStore()
    )
    monkeypatch.setattr(retention, "AuditLog", audit_factory)
    assert retention.main(["--apply"] if apply else []) == 0
    assert calls == (["audit"] if apply else [])
    captured = capsys.readouterr()
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert len(lines) == 1 and lines[0].startswith("cutoff=")
    assert lines[0].split()[1:] == [
        "scanned=5",
        "candidates=2",
        "active=2",
        f"deleted={2 if apply else 0}",
        f"apply={str(apply).lower()}",
    ]
    assert all(
        raw not in captured.out
        for raw in (
            ANSWER,
            MESSAGE,
            DRAFT,
            "synthetic-db",
            "synthetic-audit-dir",
            "expired",
            "old",
            "running",
            "waiting",
            "recent",
        )
    )

    assert len(audit.records) == int(apply)
    if not apply:
        assert turns.rows == before


def test_cli_missing_database_is_safe(monkeypatch, capsys):
    monkeypatch.delenv("GOVOPS_DATABASE_URL", raising=False)
    monkeypatch.setattr(
        retention, "open_checkpointer", lambda _: pytest.fail("opened DB")
    )
    assert retention.main([]) == 1
    output = capsys.readouterr()
    assert output.out == "" and output.err == "GOVOPS_DATABASE_URL required\n"


def test_cli_error_is_safe(monkeypatch, capsys):
    monkeypatch.setenv("GOVOPS_DATABASE_URL", "synthetic-db")
    monkeypatch.setattr(
        retention, "run_prune", AsyncMock(side_effect=RuntimeError("private detail"))
    )

    @asynccontextmanager
    async def opened(url):
        yield InMemorySaver()

    monkeypatch.setattr(retention, "open_checkpointer", opened)
    monkeypatch.setattr(retention, "PostgresTurnIndex", InMemoryTurnIndex)
    assert retention.main([]) == 1
    output = capsys.readouterr()
    assert output.out == "" and output.err == "ERROR checkpoint_prune_failed\n"


@async_test
async def test_apply_removes_only_successfully_deleted_thread_variants():
    saver, turns, audit = InMemorySaver(), InMemoryTurnIndex(), MemoryAuditLog()
    variants = InMemoryVariantStore()
    deleted, failed, active = (c * 32 for c in ("a", "b", "c"))
    for ident, status in (
        (deleted, "completed"),
        (failed, "completed"),
        (active, "running"),
    ):
        _, ts = await saved_thread(saver, ident, status)
        await turns.add(
            turn_id=ident,
            conversation_id=ident,
            owner_sub="synthetic-owner",
            turn_index=1,
            created_at=ts.isoformat(),
        )
        await variants.put(
            conversation_id=ident,
            variant="v2",
            bucket=0,
            pct_bp=10000,
            assigned_at=ts.isoformat(),
        )
    delete = saver.adelete_thread

    async def fail_one(thread_id):
        if thread_id == failed:
            raise RuntimeError("synthetic private detail")
        await delete(thread_id)

    saver.adelete_thread = fail_one
    remove_variants = variants.delete

    async def after_turns(ids):
        assert ids == (deleted,)
        assert await turns.conversation_of(deleted) is None
        assert audit.records[0]["pruned_count"] == 2
        return await remove_variants(ids)

    variants.delete = AsyncMock(side_effect=after_turns)
    result = await retention.run_prune(
        saver, turns, audit, now=ts + timedelta(days=8), apply=True, variants=variants
    )
    assert result.deleted_ids == (deleted,)
    variants.delete.assert_awaited_once_with((deleted,))
    assert await variants.get_many([deleted, failed, active]) == {
        failed: "v2",
        active: "v2",
    }
    assert await saver.aget_tuple({"configurable": {"thread_id": failed}})


@pytest.mark.parametrize("mode", ["dry_run", "no_candidates", "all_failed"])
@async_test
async def test_variants_delete_is_not_called_without_deleted_threads(mode):
    saver, turns, audit, now = await fixture_threads()
    if mode == "no_candidates":
        saver, turns = InMemorySaver(), InMemoryTurnIndex()
    elif mode == "all_failed":
        saver.adelete_thread = AsyncMock(side_effect=RuntimeError("synthetic failure"))
    variants = SimpleNamespace(delete=AsyncMock())
    result = await retention.run_prune(
        saver, turns, audit, now=now, apply=mode != "dry_run", variants=variants
    )
    assert result.deleted_ids == ()
    variants.delete.assert_not_called()


@async_test
async def test_explicit_none_variants_preserves_pruning():
    saver, turns, audit, now = await fixture_threads()
    result = await retention.run_prune(
        saver, turns, audit, now=now, apply=True, variants=None
    )
    assert result.deleted_ids == ("expired", "old")
    assert await turns.conversation_of("old-turn-1") is None
    assert len(audit.records) == 1


@async_test
async def test_startup_passes_variants_as_keyword(monkeypatch):
    prune = AsyncMock()
    monkeypatch.setattr(retention, "run_prune", prune)
    service, saver, turns = SimpleNamespace(audit=MemoryAuditLog()), object(), object()
    variants = InMemoryVariantStore()
    await retention.startup_prune(service, saver, turns, variants)
    args, kwargs = prune.call_args
    assert args == (saver, turns)
    assert kwargs["variants"] is variants
    assert kwargs["audit"] is service.audit and kwargs["apply"] is True
    assert kwargs["now"].utcoffset() == timedelta(0)
