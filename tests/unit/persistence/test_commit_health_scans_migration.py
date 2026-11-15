"""The commit-health marker table upgrades on both native startup paths."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from alembic import command

from repowise.core.persistence.database import create_engine, init_db
from repowise.core.persistence.models import GitCommitHealthScan
from tests.unit.persistence.test_wiki_pages_content_hash_migration import _alembic_config


@pytest.mark.parametrize("warm_start", [False, True])
def test_upgrade_creates_the_marker_table_once(tmp_path: Path, warm_start: bool) -> None:
    path = tmp_path / "wiki.db"
    config = _alembic_config(path)
    command.upgrade(config, "0121")
    if warm_start:
        # init_db's additive reconciler may create the table before the migration runs.
        async def warm() -> None:
            engine = create_engine(f"sqlite+aiosqlite:///{path}")
            try:
                await init_db(engine)
            finally:
                await engine.dispose()

        asyncio.run(warm())

    command.upgrade(config, "0122")

    table = GitCommitHealthScan.__tablename__
    with sqlite3.connect(path) as connection:
        columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
        assert {row[1] for row in columns} == {
            c.name for c in GitCommitHealthScan.__table__.columns
        }
    command.downgrade(config, "0121")
    with sqlite3.connect(path) as connection:
        assert connection.execute(f"PRAGMA table_info({table})").fetchall() == []
