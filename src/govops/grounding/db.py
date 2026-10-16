"""Open existing checkpoints with database-enforced read-only connections."""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit, urlunsplit

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row

READONLY_OPTIONS = "-c default_transaction_read_only=on"
DBNAME_RE = r"^[a-z_][a-z0-9_]{0,62}$"


def url_with_dbname(url: str, dbname: str) -> str:
    """Replace only the database path, preserving encoded credentials verbatim."""
    if not isinstance(dbname, str) or re.fullmatch(DBNAME_RE, dbname) is None:
        raise ValueError("invalid_dbname")
    try:
        parts = urlsplit(url)
        return urlunsplit(parts._replace(path="/" + dbname))
    except (ValueError, TypeError):
        raise ValueError("invalid_database_url") from None


def readonly_connect_kwargs() -> dict:
    """Match the installed saver connection defaults, adding read-only mode."""
    return {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
        "options": READONLY_OPTIONS,
    }


@asynccontextmanager
async def open_readonly_saver(url: str) -> AsyncIterator[AsyncPostgresSaver]:
    """Never create schema; suppress connection details on open/close failures."""
    try:
        conn = await psycopg.AsyncConnection.connect(url, **readonly_connect_kwargs())
    except Exception:  # noqa: BLE001 — backend messages may contain credentials
        raise RuntimeError("readonly_connect_failed") from None
    try:
        try:
            saver = AsyncPostgresSaver(conn)
        except Exception:  # noqa: BLE001 — constructor failures are opaque too
            raise RuntimeError("readonly_connect_failed") from None
        yield saver
    finally:
        try:
            await conn.close()
        except Exception:  # noqa: BLE001 — never expose connection information
            raise RuntimeError("readonly_connect_failed") from None
