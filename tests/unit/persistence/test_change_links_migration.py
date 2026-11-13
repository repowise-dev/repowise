"""The change link tables upgrade on both native startup paths and link stored changes."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command

from repowise.core.persistence.database import create_engine, init_db
from tests.unit.persistence.test_wiki_pages_content_hash_migration import _alembic_config


@pytest.mark.parametrize("warm_start", [False, True])
def test_upgrade_creates_and_backfills_the_link_tables(tmp_path: Path, warm_start: bool) -> None:
    path = tmp_path / "wiki.db"
    config = _alembic_config(path)
    command.upgrade(config, "0123")
    with sqlite3.connect(path) as db:
        db.execute("insert into repositories (id, name, local_path) values ('r', 'r', '/x')")
        db.execute(
            "insert into changes (id, repository_id, change_id, identity_kind, identity_value,"
            " shape, forge, title, head_sha, author_name, author_email, commit_shas_json,"
            " files_json, files_changed, lines_added, lines_deleted, category, created_at,"
            " updated_at) values ('c', 'r', 'pr-1', 'pr', '1', 'merge', 'github', 't', 'h', '',"
            " '', ?, ?, 1, 0, 0, 'other', '2026-01-01', '2026-01-01')",
            (json.dumps(["a", "b"]), json.dumps([{"path": "x.py", "added": 1, "deleted": 0}])),
        )
    if warm_start:

        async def warm() -> None:
            engine = create_engine(f"sqlite+aiosqlite:///{path}")
            try:
                await init_db(engine)
            finally:
                await engine.dispose()

        asyncio.run(warm())

    command.upgrade(config, "0124")

    with sqlite3.connect(path) as db:
        assert db.execute("select sha from change_commits order by sha").fetchall() == [("a",), ("b",)]
        assert db.execute("select file_path from change_files").fetchall() == [("x.py",)]
    command.downgrade(config, "0123")
    with sqlite3.connect(path) as db:
        assert db.execute("pragma table_info(change_commits)").fetchall() == []
