"""A tombstoned page leaves the full-text index instead of holding a slot.

A tombstone documents a file that no longer exists, and every serving layer
already drops one: hydration in the answer pipeline discards it, and the
search tools filter it out. But retrieval fetches a fixed number of rows
*before* any of that runs, so a tombstone still occupies one of those slots
and pushes a real candidate out of the fetch entirely. The page cannot be an
answer either way; the cost is the page it displaces.

Deleting the row is the only fix that works before the fetch. Filtering
afterwards is what already happens, and it is too late.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.crud import upsert_page, upsert_repository
from repowise.core.persistence.database import init_db
from repowise.core.persistence.search import FullTextSearch
from repowise.core.pipeline.persist import mark_tombstone_pages, tombstone_candidates


@pytest.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(eng)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess


async def _seed(session, fts: FullTextSearch, *paths: str) -> str:
    repo = await upsert_repository(session, name="r", local_path="/tmp/r")
    await session.commit()
    for path in paths:
        await upsert_page(
            session,
            page_id=f"file_page:{path}",
            repository_id=repo.id,
            page_type="file_page",
            title=f"File: {path}",
            content=f"# Overview\n\nThe module at {path} handles xylophone tuning.",
            summary=f"What {path} does.",
            target_path=path,
            source_hash="h",
            model_name="mock",
            provider_name="mock",
        )
        await fts.index(
            f"file_page:{path}",
            f"File: {path}",
            f"# Overview\n\nThe module at {path} handles xylophone tuning.",
            summary=f"What {path} does.",
            target_path=path,
        )
    await session.commit()
    return repo.id


async def test_marking_returns_the_page_ids_it_marked(session, engine):
    """The caller cannot delete rows it has not been told about.

    The count this used to return is derivable from the list; the list is not
    derivable from the count.
    """
    fts = FullTextSearch(engine)
    await fts.ensure_index()
    repo_id = await _seed(session, fts, "src/gone.py", "src/kept.py")

    marked = await mark_tombstone_pages(session, repo_id, [("src/gone.py", [])])

    assert marked == ["file_page:src/gone.py"]


async def test_a_tombstoned_page_is_deleted_from_the_full_text_index(session, engine):
    fts = FullTextSearch(engine)
    await fts.ensure_index()
    repo_id = await _seed(session, fts, "src/gone.py", "src/kept.py")
    assert len(await fts.search("xylophone", limit=10)) == 2

    marked = await mark_tombstone_pages(session, repo_id, [("src/gone.py", [])])
    await session.commit()
    await fts.delete_many(marked)

    assert [r.page_id for r in await fts.search("xylophone", limit=10)] == ["file_page:src/kept.py"]


async def test_marking_nothing_returns_an_empty_list(session, engine):
    """An empty list, never ``None`` — the caller passes it straight to delete."""
    fts = FullTextSearch(engine)
    await fts.ensure_index()
    repo_id = await _seed(session, fts, "src/kept.py")

    assert await mark_tombstone_pages(session, repo_id, []) == []
    assert await mark_tombstone_pages(session, repo_id, [("src/never-existed.py", [])]) == []


async def test_a_renamed_file_is_dropped_under_its_old_path(session, engine):
    """A rename tombstones the old page, which must leave the index too.

    The new path gets its own page on the next run. Leaving the old one
    searchable means both are candidates for the same file, and only one of
    them describes code that exists.
    """
    fts = FullTextSearch(engine)
    await fts.ensure_index()
    repo_id = await _seed(session, fts, "src/old.py")
    diffs = [SimpleNamespace(status="renamed", path="src/new.py", old_path="src/old.py")]

    marked = await mark_tombstone_pages(session, repo_id, tombstone_candidates(diffs))
    await session.commit()
    await fts.delete_many(marked)

    assert await fts.search("xylophone", limit=10) == []


async def _spotlight(session, repo_id: str, target: str) -> str:
    page_id = f"symbol_spotlight:{target}"
    await upsert_page(
        session,
        page_id=page_id,
        repository_id=repo_id,
        page_type="symbol_spotlight",
        title=f"Symbol: {target}",
        content="Spotlight.",
        summary="Spotlight.",
        target_path=target,
        source_hash="h",
        model_name="mock",
        provider_name="mock",
    )
    return page_id


async def _status(session, page_id: str) -> tuple[str, list[str] | None]:
    import json

    from repowise.core.persistence.models import Page

    page = await session.get(Page, page_id)
    return page.freshness_status, json.loads(page.metadata_json or "{}").get("successor_paths")


async def test_a_deleted_files_spotlight_pages_are_tombstoned_and_returned(session, engine):
    """A symbol spotlight page belongs to its file: when the file goes, so does
    the page, and the caller is told about it so the index drops it too (#3457)."""
    fts = FullTextSearch(engine)
    await fts.ensure_index()
    repo_id = await _seed(session, fts, "pkg/gone.py", "pkg/gone.pyx")
    gone = await _spotlight(session, repo_id, "pkg/gone.py::helper")
    sibling = await _spotlight(session, repo_id, "pkg/gone.pyx::other")
    await session.commit()

    marked = await mark_tombstone_pages(session, repo_id, [("pkg/gone.py", ["pkg/new.py"])])

    assert sorted(marked) == ["file_page:pkg/gone.py", gone]
    assert await _status(session, gone) == ("tombstone", ["pkg/new.py"])
    assert (await _status(session, sibling))[0] == "fresh"


async def test_a_path_is_matched_literally_not_as_a_like_pattern(session, engine):
    """``_`` and ``%`` in a path are not wildcards: deleting ``a_b.py`` leaves
    the spotlight pages of ``aXb.py`` alone."""
    fts = FullTextSearch(engine)
    await fts.ensure_index()
    repo_id = await _seed(session, fts, "pkg/a_b.py", "pkg/aXb.py")
    gone = await _spotlight(session, repo_id, "pkg/a_b.py::f")
    other = await _spotlight(session, repo_id, "pkg/aXb.py::f")
    await session.commit()

    marked = await mark_tombstone_pages(session, repo_id, [("pkg/a_b.py", [])])

    assert sorted(marked) == ["file_page:pkg/a_b.py", gone]
    assert (await _status(session, other))[0] == "fresh"


async def test_a_case_only_sibling_keeps_its_spotlight_pages(session, engine):
    """SQLite LIKE ignores ASCII case: deleting ``pkg/Foo.py`` must not
    tombstone the spotlight pages of a live ``pkg/foo.py``."""
    fts = FullTextSearch(engine)
    await fts.ensure_index()
    repo_id = await _seed(session, fts, "pkg/Foo.py", "pkg/foo.py")
    gone = await _spotlight(session, repo_id, "pkg/Foo.py::f")
    kept = await _spotlight(session, repo_id, "pkg/foo.py::f")
    await session.commit()

    marked = await mark_tombstone_pages(session, repo_id, [("pkg/Foo.py", [])])

    assert sorted(marked) == ["file_page:pkg/Foo.py", gone]
    assert (await _status(session, kept))[0] == "fresh"
