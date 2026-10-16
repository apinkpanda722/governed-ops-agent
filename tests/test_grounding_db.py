"""Grounding DB access never prepares or changes the checkpoint schema."""

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from psycopg.rows import dict_row

from govops.grounding import db


def test_readonly_connection_options_and_no_setup(monkeypatch):
    conn = Mock(close=AsyncMock())
    connect = AsyncMock(return_value=conn)
    setup = AsyncMock()
    saver = Mock(setup=setup, asetup=setup)
    factory = Mock(return_value=saver)
    monkeypatch.setattr(db.psycopg.AsyncConnection, "connect", connect)
    monkeypatch.setattr(db.AsyncPostgresSaver, "setup", setup)
    monkeypatch.setattr(db.AsyncPostgresSaver, "asetup", setup, raising=False)
    monkeypatch.setattr(db, "AsyncPostgresSaver", factory)

    async def run():
        async with db.open_readonly_saver("synthetic-db-url") as opened:
            assert opened is saver

    asyncio.run(run())
    kwargs = connect.call_args.kwargs
    assert kwargs == db.readonly_connect_kwargs()
    assert "default_transaction_read_only=on" in kwargs["options"]
    assert kwargs["autocommit"] is True
    assert kwargs["prepare_threshold"] == 0
    assert kwargs["row_factory"] is dict_row
    factory.assert_called_once_with(conn)
    setup.assert_not_called()
    conn.close.assert_awaited_once()


@pytest.mark.parametrize("failure", ["connect", "close", "construct"])
def test_connection_failures_are_sanitized(monkeypatch, failure):
    url = "synthetic-sensitive-connection-string"
    conn = Mock(close=AsyncMock())
    connect = AsyncMock(return_value=conn)
    factory = Mock()
    if failure == "connect":
        connect.side_effect = ValueError(url)
    elif failure == "close":
        conn.close.side_effect = ValueError(url)
    else:
        factory.side_effect = ValueError(url)
    monkeypatch.setattr(db.psycopg.AsyncConnection, "connect", connect)
    monkeypatch.setattr(db, "AsyncPostgresSaver", factory)

    async def run():
        async with db.open_readonly_saver(url):
            pass

    with pytest.raises(RuntimeError, match="^readonly_connect_failed$") as caught:
        asyncio.run(run())
    assert url not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
    if failure != "connect":
        conn.close.assert_awaited_once()


def test_body_errors_still_close_the_connection(monkeypatch):
    conn = Mock(close=AsyncMock())
    monkeypatch.setattr(
        db.psycopg.AsyncConnection, "connect", AsyncMock(return_value=conn)
    )
    monkeypatch.setattr(db, "AsyncPostgresSaver", Mock())

    async def run():
        async with db.open_readonly_saver("synthetic-url"):
            raise LookupError("synthetic-body-error")

    with pytest.raises(LookupError):
        asyncio.run(run())
    conn.close.assert_awaited_once()


def test_all_grounding_modules_forbid_schema_setup_and_write_factory():
    root = Path(__file__).resolve().parents[1] / "src/govops/grounding"
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                assert all(
                    "open_checkpointer" not in alias.name for alias in node.names
                )
                if isinstance(node, ast.ImportFrom):
                    assert "open_checkpointer" not in (node.module or "")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"setup", "asetup"}
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"setup", "asetup", "open_checkpointer"}
