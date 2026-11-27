"""Doctor suggests a VACUUM once over a quarter of wiki.db is free pages, and never runs one."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from repowise.cli.commands.doctor_cmd._checks import core
from repowise.cli.commands.doctor_cmd._checks.context import CheckContext


def _store(repo: Path, *, keep: int) -> Path:
    """A wiki.db of 100 equal rows with all but *keep* deleted afterwards."""
    db = repo / ".repowise" / "wiki.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (body BLOB)")
    conn.executemany("INSERT INTO t VALUES (?)", [(b"x" * 8000,) for _ in range(100)])
    conn.commit()
    conn.execute("DELETE FROM t WHERE rowid > ?", (keep,))
    conn.commit()
    conn.close()
    return db


@pytest.fixture(autouse=True)
def _local_store(monkeypatch):
    monkeypatch.setattr(core, "db_configured", lambda: False)


def test_a_mostly_free_store_gets_the_command_and_is_left_alone(tmp_path):
    db = _store(tmp_path, keep=40)
    size = db.stat().st_size

    row = core._wiki_db_free_row(CheckContext(tmp_path, tmp_path / ".repowise"))

    assert row is not None
    assert row.id == "wiki-db-free-space"
    assert row.level == "warn" and row.ok
    assert "VACUUM" in row.fix and db.resolve().as_posix() in row.fix
    assert row.data["free_bytes"] > row.data["file_bytes"] // 4
    assert db.stat().st_size == size


def test_a_store_mostly_in_use_shows_no_row(tmp_path):
    _store(tmp_path, keep=90)
    assert core._wiki_db_free_row(CheckContext(tmp_path, tmp_path / ".repowise")) is None


def test_no_store_shows_no_row(tmp_path):
    assert core._wiki_db_free_row(CheckContext(tmp_path, tmp_path / ".repowise")) is None
