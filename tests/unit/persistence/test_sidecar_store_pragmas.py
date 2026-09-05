"""The synchronous sidecar stores must open under a contended WAL switch.

Each store used to issue ``PRAGMA journal_mode=WAL`` bare, before any busy
timeout. The switch needs a short exclusive lock, so a reader holding an open
transaction on the same file made every constructor raise ``database is
locked``. That is the shape a post-commit update overlapping an editor hook
produces, and it is why the hook could not default on until these were
guarded.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from repowise.core.distill import store as distill_store
from repowise.core.precedent import store as precedent_store
from repowise.core.sessions import staging
from repowise.core.sqlite_pragmas import apply_sqlite_pragmas


def _hold_read_lock(db_path: Path) -> sqlite3.Connection:
    """Open a transaction that holds a shared lock until the connection closes.

    In rollback-journal mode a shared lock is enough to make the WAL switch
    fail, so the store is first forced back out of WAL.
    """
    reset = sqlite3.connect(db_path)
    reset.execute("PRAGMA journal_mode=DELETE")
    reset.close()
    holder = sqlite3.connect(db_path, isolation_level=None)
    holder.execute("BEGIN")
    holder.execute("SELECT count(*) FROM sqlite_master")
    return holder


def test_helper_swallows_the_failed_switch_and_keeps_the_connection(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    seed = sqlite3.connect(db_path)
    seed.execute("CREATE TABLE t (x)")
    seed.commit()
    seed.close()
    holder = _hold_read_lock(db_path)
    try:
        conn = sqlite3.connect(db_path)
        assert apply_sqlite_pragmas(conn, 50) is False
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 50
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 0
        conn.close()
    finally:
        holder.close()


def test_helper_switches_to_wal_when_uncontended(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "t.db")
    assert apply_sqlite_pragmas(conn, 50) is True
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


@pytest.mark.parametrize(
    ("module", "open_store"),
    [
        (distill_store, lambda p: distill_store.OmissionStore(p)),
        (precedent_store, lambda p: precedent_store.EpisodeStore(p)),
        (staging, lambda p: staging.SessionStagingStore(p)),
    ],
    ids=["distill", "precedent", "staging"],
)
def test_store_opens_while_another_connection_holds_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module, open_store
) -> None:
    monkeypatch.setattr(module, "_BUSY_TIMEOUT_MS", 50)
    db_path = tmp_path / "store.db"
    first = open_store(db_path)
    first._conn.close()

    holder = _hold_read_lock(db_path)
    try:
        store = open_store(db_path)
    finally:
        holder.close()
    assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 50
    assert store._conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] > 0
    store._conn.close()
