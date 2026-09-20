"""Verify that file-backed SQLite engines come up with WAL, busy_timeout, and FK
constraints enabled. These are the settings that make concurrent
``repowise update`` invocations on the same workspace stop colliding (issue #95).
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text

from repowise.core.persistence import create_engine, init_db
from repowise.core.persistence.database import (
    _make_pragma_listener,
    _sqlite_pragmas,
)


async def _read_pragma(engine, pragma: str) -> str:
    async with engine.connect() as conn:
        result = await conn.execute(text(f"PRAGMA {pragma}"))
        row = result.fetchone()
        return str(row[0]) if row is not None else ""


@pytest.mark.asyncio
async def test_file_sqlite_engine_uses_wal(tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}"
    engine = create_engine(db_url)
    try:
        await init_db(engine)
        mode = await _read_pragma(engine, "journal_mode")
        assert mode.lower() == "wal"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_file_sqlite_engine_sets_busy_timeout(tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}"
    engine = create_engine(db_url)
    try:
        await init_db(engine)
        timeout = await _read_pragma(engine, "busy_timeout")
        assert int(timeout) >= 1000
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_busy_timeout_override_is_applied(tmp_path: Path) -> None:
    """A custom ``busy_timeout_ms`` must reach the connection pragma so the
    cost tracker's best-effort engine fails fast under contention instead of
    blocking the full 30s default window (issue #326)."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}"
    engine = create_engine(db_url, busy_timeout_ms=2000)
    try:
        await init_db(engine)
        timeout = await _read_pragma(engine, "busy_timeout")
        assert int(timeout) == 2000
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_default_busy_timeout_unchanged(tmp_path: Path) -> None:
    """Without an override the engine keeps the 30s default — the headroom that
    legitimate bulk graph-edge writes rely on."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}"
    engine = create_engine(db_url)
    try:
        await init_db(engine)
        timeout = await _read_pragma(engine, "busy_timeout")
        assert int(timeout) == 30000
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_file_sqlite_engine_enforces_foreign_keys(tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}"
    engine = create_engine(db_url)
    try:
        await init_db(engine)
        fk = await _read_pragma(engine, "foreign_keys")
        assert int(fk) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_writers_do_not_lock(tmp_path: Path) -> None:
    """Two concurrent writers against the same file-backed SQLite database
    should both succeed once WAL plus busy_timeout are active. Without the fix
    this raises ``sqlite3.OperationalError: database is locked``."""

    db_path = tmp_path / "wiki.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_engine(db_url)
    try:
        await init_db(engine)
        # Seed one row so the second writer has something to update.
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE IF NOT EXISTS t (k INTEGER PRIMARY KEY, v INTEGER)")
            )
            await conn.execute(text("INSERT INTO t (k, v) VALUES (1, 0)"))
    finally:
        await engine.dispose()

    async def write_in_own_engine(value: int) -> None:
        own = create_engine(db_url)
        try:
            async with own.begin() as conn:
                await conn.execute(text("UPDATE t SET v = :v WHERE k = 1"), {"v": value})
        finally:
            await own.dispose()

    # Without WAL plus busy_timeout one of these would raise OperationalError.
    await asyncio.gather(write_in_own_engine(1), write_in_own_engine(2))


def test_pragmas_survive_a_fresh_sync_open(tmp_path: Path) -> None:
    """After the async engine creates the database, opening it through the raw
    sqlite3 driver should still report WAL because journal_mode is a persistent
    file-level setting."""

    async def _create() -> Path:
        db_path = tmp_path / "wiki.db"
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            await init_db(engine)
        finally:
            await engine.dispose()
        return db_path

    db_path = asyncio.run(_create())
    conn = sqlite3.connect(db_path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def _seed_non_wal_db(db_path: Path) -> None:
    """Create a store left in ``delete`` journal mode, as an older repowise, a
    third-party tool, or a filesystem that refused the first WAL switch would."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("CREATE TABLE t (k INTEGER PRIMARY KEY, v INTEGER)")
        conn.execute("INSERT INTO t (k, v) VALUES (1, 0)")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_read_survives_a_writer_holding_a_non_wal_store(tmp_path: Path) -> None:
    """A reader must not die at connect time because the WAL switch lost a race.

    SQLite does not route the ``journal_mode`` change through the busy handler:
    against a held write transaction it returns SQLITE_BUSY immediately, whatever
    ``busy_timeout`` says. The pragma is a no-op on a store already in WAL, so
    only a store still in ``delete`` reaches this — and there the old listener
    raised ``OperationalError: database is locked`` out of the ``connect`` event,
    killing the connection before any query ran, including reads that would have
    succeeded. This is the MCP server's ``OperationalError`` (issue #2059).
    """
    db_path = tmp_path / "wiki.db"
    _seed_non_wal_db(db_path)

    writer = sqlite3.connect(db_path, timeout=30)
    writer.execute("PRAGMA busy_timeout=30000")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("UPDATE t SET v = 99 WHERE k = 1")
    try:
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            async with engine.connect() as conn:
                rows = (await conn.execute(text("SELECT v FROM t WHERE k = 1"))).fetchall()
            assert rows == [(0,)]
        finally:
            await engine.dispose()
    finally:
        writer.rollback()
        writer.close()


@pytest.mark.asyncio
async def test_wal_switch_still_applies_once_the_writer_releases(tmp_path: Path) -> None:
    """Tolerating the busy switch must not mean abandoning WAL. Once no writer
    holds the store, the next connection upgrades it as before."""
    db_path = tmp_path / "wiki.db"
    _seed_non_wal_db(db_path)

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        writer = sqlite3.connect(db_path, timeout=30)
        writer.execute("PRAGMA busy_timeout=30000")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE t SET v = 99 WHERE k = 1")
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT v FROM t WHERE k = 1"))
        finally:
            writer.rollback()
            writer.close()

        assert (await _read_pragma(engine, "journal_mode")).lower() == "wal"
    finally:
        await engine.dispose()


def test_busy_timeout_is_the_first_pragma_applied() -> None:
    """Everything after ``busy_timeout`` on a connection inherits its retry
    window, so it has to lead the list rather than arrive third."""
    assert _sqlite_pragmas(30000)[0][0] == "busy_timeout"


def _listener_with_journal_mode_error(exc: Exception):
    """Drive the connect listener against a cursor whose WAL switch fails."""
    listener = _make_pragma_listener(30000)
    applied: list[str] = []

    class _Cursor:
        def execute(self, sql: str) -> None:
            if "journal_mode" in sql:
                raise exc
            applied.append(sql)

        def close(self) -> None:
            return None

    class _Conn:
        def cursor(self) -> _Cursor:
            return _Cursor()

    listener(_Conn(), None)
    return applied


def test_a_readonly_store_still_gets_a_usable_connection() -> None:
    """The other way the defensive re-issue fails: the store or its directory is
    read-only. Reads from it are still perfectly good, so this must not raise
    either - and the pragmas after it must still be applied."""
    applied = _listener_with_journal_mode_error(
        sqlite3.OperationalError("attempt to write a readonly database")
    )
    assert any("foreign_keys" in sql for sql in applied)


def test_a_non_operational_pragma_failure_still_raises() -> None:
    """Tolerance is scoped to what the defensive re-issue can legitimately hit.
    A failure that is not an ``OperationalError`` is not that, and must surface.
    """
    listener = _make_pragma_listener(30000)

    class _Cursor:
        def execute(self, sql: str) -> None:
            if "journal_mode" in sql:
                raise sqlite3.ProgrammingError("cannot operate on a closed database")

        def close(self) -> None:
            return None

    class _Conn:
        def cursor(self) -> _Cursor:
            return _Cursor()

    with pytest.raises(sqlite3.ProgrammingError):
        listener(_Conn(), None)
