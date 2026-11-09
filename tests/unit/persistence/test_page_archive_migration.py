"""Archive schema upgrades preserve live stores on both native startup paths."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from alembic import command

from repowise.core.persistence.database import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
)
from repowise.core.persistence.models import (
    Page,
    PageArchive,
    PageVersion,
    PageVersionArchive,
    Repository,
)
from tests.unit.persistence.helpers import insert_repo, make_page_kwargs
from tests.unit.persistence.test_wiki_pages_content_hash_migration import _alembic_config


def _live_rows(path: Path) -> dict:
    with sqlite3.connect(path) as connection:
        return {
            table: connection.execute(f"SELECT * FROM {table}").fetchall()
            for table in (Repository.__tablename__, Page.__tablename__, PageVersion.__tablename__)
        }


@pytest.mark.parametrize("warm_start", [False, True])
def test_upgrade_handles_migrated_and_additive_stores(tmp_path: Path, warm_start: bool) -> None:
    from repowise.core.persistence import crud

    path = tmp_path / "wiki.db"
    config = _alembic_config(path)
    command.upgrade(config, "0120")

    async def seed() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{path}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                repo = await insert_repo(session)
                kwargs = make_page_kwargs(repo.id, content="Original migration prose")
                await crud.upsert_page(session, **kwargs)
                await crud.upsert_page(session, **{**kwargs, "content": "Current migration prose"})
            if warm_start:
                await init_db(engine)
        finally:
            await engine.dispose()

    asyncio.run(seed())
    original = _live_rows(path)
    command.upgrade(config, "head")
    assert _live_rows(path) == original
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0121",)
        for model in (PageArchive, PageVersionArchive):
            columns = connection.execute(f"PRAGMA table_info({model.__tablename__})").fetchall()
            assert {row[1] for row in columns} == {c.name for c in model.__table__.columns}
        indexes = connection.execute("PRAGMA index_list(page_archive)").fetchall()
        assert {row[1] for row in indexes} >= {
            "ix_page_archive_repo_page",
            "ix_page_archive_repo_retired",
        }
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    command.downgrade(config, "0120")
    assert _live_rows(path) == original
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA table_info(page_archive)").fetchall() == []
        assert connection.execute("PRAGMA table_info(page_version_archive)").fetchall() == []


def test_populated_archive_recovers_after_schema_downgrade(tmp_path: Path) -> None:
    from repowise.core.persistence import crud

    path = tmp_path / "wiki.db"
    recovery = tmp_path / "recovery.db"
    config = _alembic_config(path)
    command.upgrade(config, "head")

    async def retire() -> str:
        engine = create_engine(f"sqlite+aiosqlite:///{path}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                repo = await insert_repo(session)
                kwargs = make_page_kwargs(repo.id, content="Original retained prose")
                await crud.upsert_page(session, **kwargs)
                await crud.upsert_page(session, **{**kwargs, "content": "Retired current prose"})
                page = await crud.get_page(session, kwargs["page_id"])
                page.human_notes = "Retained human insight"
                await session.flush()
                await crud.retire_pages(session, repo.id, [page.id], "superseded")
                return repo.id
        finally:
            await engine.dispose()

    repo_id = asyncio.run(retire())
    with sqlite3.connect(path) as source, sqlite3.connect(recovery) as saved:
        source.backup(saved)
        original = source.iterdump()
        complete_original = list(original)

    # Schema downgrade is destructive, not data recovery. Preserve a populated
    # database first and restore it through SQLite's native backup protocol.
    command.downgrade(config, "0120")
    with sqlite3.connect(recovery) as saved, sqlite3.connect(path) as destination:
        assert destination.execute("PRAGMA table_info(page_archive)").fetchall() == []
        saved.backup(destination)
        assert list(destination.iterdump()) == complete_original
        assert destination.execute("PRAGMA foreign_key_check").fetchall() == []

    async def read_retirement() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{path}")
        try:
            await init_db(engine)
            async with get_session(create_session_factory(engine)) as session:
                page = await crud.get_retired_page(session, repo_id, "file_page:src/main.py")
                versions = await crud.get_retired_page_versions(session, repo_id, page.id)
                detail = crud.retired_page_detail(page, versions)
                assert detail["page"]["content"] == "Retired current prose"
                assert detail["page"]["human_notes"] == "Retained human insight"
                assert [row["content"] for row in detail["versions"]] == ["Original retained prose"]
                assert await crud.get_page(session, "file_page:src/main.py") is None
        finally:
            await engine.dispose()

    asyncio.run(read_retirement())
