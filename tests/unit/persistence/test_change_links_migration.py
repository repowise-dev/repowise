"""The change link tables upgrade on both native startup paths and link stored changes."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command

from repowise.core.persistence.crud import changes as crud_changes
from repowise.core.persistence.database import create_engine, init_db
from tests.unit.persistence.test_wiki_pages_content_hash_migration import _alembic_config


def _insert_change(db: sqlite3.Connection, n: int, shas: list[str], paths: list[str]) -> None:
    db.execute(
        "insert into changes (id, repository_id, change_id, identity_kind, identity_value,"
        " shape, forge, title, head_sha, author_name, author_email, commit_shas_json,"
        " files_json, files_changed, lines_added, lines_deleted, category, created_at,"
        " updated_at) values (?, 'r', ?, 'pr', ?, 'merge', 'github', 't', 'h', '',"
        " '', ?, ?, 1, 0, 0, 'other', '2026-01-01', '2026-01-01')",
        (
            f"c{n}",
            f"pr-{n}",
            str(n),
            json.dumps(shas),
            json.dumps([{"path": p, "added": 1, "deleted": 0} for p in paths]),
        ),
    )


def _store_before_links(tmp_path: Path):
    path = tmp_path / "wiki.db"
    config = _alembic_config(path)
    command.upgrade(config, "0126")
    with sqlite3.connect(path) as db:
        db.execute("insert into repositories (id, name, local_path) values ('r', 'r', '/x')")
        _insert_change(db, 1, ["a", "b"], ["x.py"])
    return path, config


def _init_db(path: Path) -> None:
    async def run() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{path}")
        try:
            await init_db(engine)
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("warm_start", [False, True])
def test_upgrade_creates_and_backfills_the_link_tables(tmp_path: Path, warm_start: bool) -> None:
    path, config = _store_before_links(tmp_path)
    if warm_start:
        _init_db(path)

    command.upgrade(config, "0127")

    with sqlite3.connect(path) as db:
        assert db.execute("select sha from change_commits order by sha").fetchall() == [("a",), ("b",)]
        assert db.execute("select file_path from change_files").fetchall() == [("x.py",)]
    command.downgrade(config, "0126")
    with sqlite3.connect(path) as db:
        assert db.execute("pragma table_info(change_commits)").fetchall() == []


def test_init_db_links_an_existing_store_when_it_creates_the_tables(tmp_path: Path) -> None:
    """A local store never runs Alembic: the reconciler fills the links itself,
    so the delta and hotspot filters answer straight after an upgrade."""
    path, _ = _store_before_links(tmp_path)

    _init_db(path)

    with sqlite3.connect(path) as db:
        assert db.execute("select sha from change_commits order by sha").fetchall() == [("a",), ("b",)]
        assert db.execute("select file_path from change_files").fetchall() == [("x.py",)]


def test_link_unlinked_changes_reads_keyset_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, config = _store_before_links(tmp_path)
    command.upgrade(config, "0127")
    with sqlite3.connect(path) as db:
        db.execute("delete from change_commits")
        db.execute("delete from change_files")
        for n in range(2, 7):
            # A change with no commits links only its paths.
            _insert_change(db, n, [] if n == 3 else [f"s{n}"], [f"f{n}.py"])
        # One with neither stays unlinked, so paging has to move past it.
        _insert_change(db, 7, [], [])
    monkeypatch.setattr(crud_changes, "_BATCH_SIZE", 2)
    pages: list[int] = []
    engine = sa.create_engine(f"sqlite:///{path}")

    @sa.event.listens_for(engine, "after_cursor_execute")
    def count_pages(conn, cursor, statement, params, context, executemany) -> None:
        if statement.lstrip().upper().startswith("SELECT") and "FROM changes" in statement:
            pages.append(1)

    try:
        with engine.begin() as conn:
            assert crud_changes.link_unlinked_changes(conn) == 5
            # Only the empty change is read again.
            assert crud_changes.link_unlinked_changes(conn, "r") == 0
    finally:
        engine.dispose()

    assert len(pages) == 5 + 2  # four pages and an empty one, then one and an empty one
    with sqlite3.connect(path) as db:
        assert db.execute("select count(*) from change_commits").fetchone() == (6,)
        assert db.execute("select count(*) from change_files").fetchone() == (6,)
