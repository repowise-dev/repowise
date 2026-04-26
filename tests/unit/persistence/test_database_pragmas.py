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
            await conn.execute(text("CREATE TABLE IF NOT EXISTS t (k INTEGER PRIMARY KEY, v INTEGER)"))
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
