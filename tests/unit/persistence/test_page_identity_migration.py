"""Stores keyed by page id alone move to ``(repository_id, id)`` without losing rows.

Both upgrade paths are covered: ``init_db`` (every local store) and Alembic
revision 0156 (PostgreSQL deployments, exercised here on SQLite). Each starts
from a store migrated to 0155, the last revision with the id-only key, holding
pages and version history.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from alembic import command

from repowise.core.persistence import crud
from repowise.core.persistence.database import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
)
from tests.unit.persistence.helpers import insert_repo, make_page_kwargs
from tests.unit.persistence.test_wiki_pages_content_hash_migration import _alembic_config

_LEGACY = "0155"


def _pk(path: Path, table: str) -> list[str]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [r[1] for r in sorted((r for r in rows if r[5]), key=lambda r: r[5])]


def _fks(path: Path) -> list[tuple]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute('PRAGMA foreign_key_list("wiki_page_versions")').fetchall()
    # (id, seq, table, from, to, ...) -> (table, from, to) per column of each key
    return sorted((r[2], r[3], r[4]) for r in rows)


def _rows(path: Path) -> dict[str, list]:
    with sqlite3.connect(path) as conn:
        return {
            "pages": sorted(
                conn.execute(
                    "SELECT repository_id, id, content, version FROM wiki_pages"
                ).fetchall()
            ),
            "versions": sorted(
                conn.execute(
                    "SELECT id, repository_id, page_id, version, content FROM wiki_page_versions"
                ).fetchall()
            ),
        }


def _legacy_store(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A 0155 store with two repositories, pages and history, keyed by id."""
    path = tmp_path / "wiki.db"
    command.upgrade(_alembic_config(path), _LEGACY)
    assert _pk(path, "wiki_pages") == ["id"]
    repos: dict[str, str] = {}

    async def seed() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{path}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                first = await insert_repo(session, name="a", local_path=str(tmp_path / "a"))
                second = await insert_repo(session, name="b", local_path=str(tmp_path / "b"))
                repos.update(a=first.id, b=second.id)
                main = make_page_kwargs(first.id, content="v1")
                await crud.upsert_page(session, **main)
                await crud.upsert_page(session, **{**main, "content": "v2"})
                await crud.upsert_page(
                    session,
                    **make_page_kwargs(
                        second.id,
                        page_id="file_page:src/other.py",
                        target_path="src/other.py",
                        content="other",
                    ),
                )
        finally:
            await engine.dispose()

    asyncio.run(seed())
    return path, repos


async def _write_shared_id(path: Path, repos: dict[str, str]) -> list[tuple]:
    """Write repo b's own ``file_page:src/main.py`` and return both rows."""
    engine = create_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with get_session(create_session_factory(engine)) as session:
            await crud.upsert_page(session, **make_page_kwargs(repos["b"], content="b main"))
        async with get_session(create_session_factory(engine)) as session:
            out = []
            for repo in ("a", "b"):
                page = await crud.get_page(
                    session, "file_page:src/main.py", repository_id=repos[repo]
                )
                out.append((repo, page.content, page.version))
            return out
    finally:
        await engine.dispose()


def _assert_rekeyed(path: Path, before: dict[str, list]) -> None:
    assert _pk(path, "wiki_pages") == ["repository_id", "id"]
    assert _fks(path) == [
        ("wiki_pages", "page_id", "id"),
        ("wiki_pages", "repository_id", "repository_id"),
    ]
    assert _rows(path) == before
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        indexes = {r[1] for r in conn.execute('PRAGMA index_list("wiki_pages")')}
        assert {"ix_wiki_pages_id", "ix_wiki_pages_parent_page_id"} <= indexes
        version_indexes = {r[1] for r in conn.execute('PRAGMA index_list("wiki_page_versions")')}
        assert "ix_wiki_page_versions_repo_page" in version_indexes
        leftovers = conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '_rekey_old_%'"
        ).fetchall()
        assert leftovers == []


def test_init_db_rekeys_a_legacy_store_and_keeps_every_row(tmp_path: Path) -> None:
    path, repos = _legacy_store(tmp_path)
    before = _rows(path)
    assert len(before["pages"]) == 2 and len(before["versions"]) == 1

    async def open_twice() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{path}")
        try:
            await init_db(engine)
            # A second open is a no-op: same key, same rows.
            await init_db(engine)
        finally:
            await engine.dispose()

    asyncio.run(open_twice())
    _assert_rekeyed(path, before)
    assert asyncio.run(_write_shared_id(path, repos)) == [("a", "v2", 2), ("b", "b main", 1)]


def test_alembic_0156_rekeys_and_downgrades(tmp_path: Path) -> None:
    path, repos = _legacy_store(tmp_path)
    before = _rows(path)
    config = _alembic_config(path)

    command.upgrade(config, "0156")
    _assert_rekeyed(path, before)

    command.downgrade(config, _LEGACY)
    assert _pk(path, "wiki_pages") == ["id"]
    assert _fks(path) == [("wiki_pages", "page_id", "id")]
    assert _rows(path) == before

    command.upgrade(config, "0156")
    assert asyncio.run(_write_shared_id(path, repos)) == [("a", "v2", 2), ("b", "b main", 1)]
    # Two repositories now hold one id, which the id-only key cannot.
    with pytest.raises(RuntimeError, match="same page id"):
        command.downgrade(config, _LEGACY)
    assert _pk(path, "wiki_pages") == ["repository_id", "id"]


def test_a_version_filed_under_the_wrong_repository_follows_its_page(tmp_path: Path) -> None:
    path, repos = _legacy_store(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE wiki_page_versions SET repository_id = ?", (repos["b"],))
    command.upgrade(_alembic_config(path), "0156")
    with sqlite3.connect(path) as conn:
        owners = conn.execute("SELECT repository_id, page_id FROM wiki_page_versions").fetchall()
    assert owners == [(repos["a"], "file_page:src/main.py")]


def test_a_fresh_store_is_born_on_the_composite_key(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"

    async def build() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{path}")
        try:
            await init_db(engine)
        finally:
            await engine.dispose()

    asyncio.run(build())
    assert _pk(path, "wiki_pages") == ["repository_id", "id"]
    assert _fks(path) == [
        ("wiki_pages", "page_id", "id"),
        ("wiki_pages", "repository_id", "repository_id"),
    ]
