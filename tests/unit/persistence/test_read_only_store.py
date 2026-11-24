"""``open_store`` and ``publishable``: a store a host serves without writing."""

from __future__ import annotations

import contextlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from repowise.core.persistence.database import (
    ReadOnlyStoreError,
    SchemaBehindError,
    create_engine,
    create_session_factory,
    init_db,
    is_read_only,
)
from repowise.core.persistence.models import AnswerCache, Page, Repository
from repowise.core.persistence.search import FullTextSearch, PageFtsState, page_fts_state
from repowise.core.workspace.registry import open_store, publishable

_NOW = datetime(2026, 11, 25, 1, 0, 0, tzinfo=UTC)
_REPO = "repo-ro"


async def _build(path: Path) -> None:
    """A store with one repository and one indexed page, closed (checkpointed)."""
    engine = create_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
    await init_db(engine)
    async with create_session_factory(engine)() as session:
        session.add(
            Repository(
                id=_REPO,
                name="ro",
                url="https://example.com/ro",
                local_path="/nowhere/ro",
                default_branch="main",
                settings_json="{}",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        await session.flush()
        session.add(
            Page(
                id="file_page:svc.py",
                repository_id=_REPO,
                page_type="file_page",
                title="svc.py",
                content="The ocelot service routes requests and stores records.",
                target_path="svc.py",
                source_hash="abc",
                model_name="mock",
                provider_name="mock",
                generation_level=2,
                confidence=1.0,
                freshness_status="fresh",
                metadata_json="{}",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        await session.commit()
    fts = FullTextSearch(engine)
    await fts.ensure_index()
    await fts.index(
        "file_page:svc.py",
        "svc.py",
        "The ocelot service routes requests, stores records and reports usage each night.",
        summary="ocelot service",
        target_path="svc.py",
    )
    await engine.dispose()


@pytest.fixture
async def store_path(tmp_path: Path) -> Path:
    path = tmp_path / "wiki.db"
    await _build(path)
    return path


def _raw(path: Path, *statements: str) -> None:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        for statement in statements:
            conn.execute(statement)
        conn.commit()


async def test_open_store_reads_and_searches(store_path: Path) -> None:
    store = await open_store(store_path, _REPO)
    try:
        assert is_read_only(store.engine)
        assert store.page_fts is PageFtsState.PRESENT
        async with store.session_factory() as session:
            titles = (await session.execute(select(Page.title))).scalars().all()
        assert titles == ["svc.py"]
        assert [h.page_id for h in await store.fts.search("ocelot")] == ["file_page:svc.py"]
        ctx = store.context("ro")
        assert (ctx.path, ctx.source, ctx.git) == (None, None, False)
        assert ctx.fts is store.fts
    finally:
        await store.dispose()


async def test_every_write_on_a_read_only_store_fails_loudly(store_path: Path) -> None:
    store = await open_store(store_path, _REPO)
    try:
        async with store.session_factory() as session:
            with pytest.raises(OperationalError, match="readonly"):
                await session.execute(update(Page).values(title="changed"))
        async with store.session_factory() as session:
            session.add(
                AnswerCache(repository_id=_REPO, question_hash="q", question="q", payload_json="{}")
            )
            with pytest.raises(OperationalError, match="readonly"):
                await session.commit()
        with pytest.raises(ReadOnlyStoreError):
            await init_db(store.engine)
        with pytest.raises(ReadOnlyStoreError):
            await store.fts.ensure_index()
        with pytest.raises(OperationalError, match="readonly"):
            await store.fts.index("x", "x", "some words about the heron service", target_path="x")
    finally:
        await store.dispose()
    # Nothing reached the file.
    with contextlib.closing(sqlite3.connect(store_path)) as conn:
        assert conn.execute("SELECT title FROM wiki_pages").fetchall() == [("svc.py",)]


async def test_open_store_refuses_an_older_schema(store_path: Path) -> None:
    _raw(store_path, f"DROP TABLE {AnswerCache.__tablename__}")
    with pytest.raises(SchemaBehindError) as raised:
        await open_store(store_path, _REPO)
    assert raised.value.code == "schema_behind"
    assert (await publishable(store_path)).problems == ("schema_behind",)
    # The refused open wrote nothing: the table is still missing.
    with contextlib.closing(sqlite3.connect(store_path)) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert AnswerCache.__tablename__ not in names


async def test_open_store_names_what_is_missing(store_path: Path, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        await open_store(tmp_path / "absent.db", _REPO)
    with pytest.raises(LookupError):
        await open_store(store_path, "repo-other")


@pytest.mark.parametrize(
    ("ddl", "state"),
    [
        (None, PageFtsState.MISSING),
        (
            "CREATE VIRTUAL TABLE page_fts USING fts5(page_id UNINDEXED, title, content)",
            PageFtsState.STALE,
        ),
    ],
)
async def test_page_fts_state_on_an_immutable_store(
    store_path: Path, ddl: str | None, state: PageFtsState
) -> None:
    _raw(store_path, "DROP TABLE page_fts", *([ddl] if ddl else []))
    store = await open_store(store_path, _REPO)
    try:
        assert store.page_fts is state
        assert await page_fts_state(store.engine) is state
        # Search degrades to empty instead of erroring on the missing or stale index.
        assert await store.fts.search("ocelot") == []
    finally:
        await store.dispose()
    assert (await publishable(store_path)).problems == (f"page_fts_{state.value}",)


async def test_publishable_on_a_clean_store(store_path: Path) -> None:
    check = await publishable(store_path)
    assert check.ok and check.problems == ()


async def test_publishable_refuses_writes_still_in_the_wal(store_path: Path) -> None:
    conn = sqlite3.connect(store_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("UPDATE wiki_pages SET title = 'pending'")
        conn.commit()
        check = await publishable(store_path)
        assert not check.ok and check.problems == ("wal_pending",)
        # What an immutable open would serve: the checkpointed file, not the WAL.
        store = await open_store(store_path, _REPO)
        try:
            async with store.session_factory() as session:
                assert (await session.execute(select(Page.title))).scalar() == "svc.py"
        finally:
            await store.dispose()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert (await publishable(store_path)).ok
    finally:
        conn.close()


async def test_publishable_other_problems(store_path: Path, tmp_path: Path) -> None:
    assert (await publishable(tmp_path / "absent.db")).problems == ("missing",)
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"not a sqlite file at all" * 100)
    assert (await publishable(garbage)).problems == ("unreadable",)
    _raw(
        store_path, "PRAGMA foreign_keys=OFF", "DELETE FROM wiki_pages", "DELETE FROM repositories"
    )
    assert (await publishable(store_path)).problems == ("no_repository",)


async def test_default_writable_store_is_unchanged(store_path: Path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{store_path.as_posix()}")
    try:
        assert not is_read_only(engine)
        await init_db(engine)
        fts = FullTextSearch(engine)
        await fts.ensure_index()
        await fts.index(
            "file_page:b.py",
            "b.py",
            "The heron worker drains the queue nightly.",
            target_path="b.py",
        )
        assert [h.page_id for h in await fts.search("heron")] == ["file_page:b.py"]
    finally:
        await engine.dispose()
