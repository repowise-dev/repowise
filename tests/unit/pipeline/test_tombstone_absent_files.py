"""A page for a file that is simply gone becomes a tombstone.

The existing tombstone path reads a diff, so it only ever sees a file that
was deleted or renamed between two commits a run compared. ``repowise init``
compares nothing — it indexes a checkout as it stands — so it has never
tombstoned anything, and a page written before its file was deleted keeps
``freshness_status='fresh'`` through every later index.

That page is the trap the tombstone exists to close: retrieval serves it,
agents cite it, and the index-age metadata says nothing is wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.crud import upsert_page, upsert_repository
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Page
from repowise.core.pipeline.persist import tombstone_absent_file_pages


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


async def _seed(session, repo_root: Path, *paths: str, page_type: str = "file_page") -> str:
    """One page per path. Only the paths written to disk actually exist."""
    repo = await upsert_repository(session, name="r", local_path=str(repo_root))
    await session.commit()
    for path in paths:
        await upsert_page(
            session,
            page_id=f"{page_type}:{path}",
            repository_id=repo.id,
            page_type=page_type,
            title=f"File: {path}",
            content=f"# Overview\n\nWhat {path} does.",
            summary=f"What {path} does.",
            target_path=path,
            source_hash="h",
            model_name="mock",
            provider_name="mock",
        )
    await session.commit()
    return repo.id


def _write(repo_root: Path, *paths: str) -> None:
    for path in paths:
        target = repo_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")


async def _status(session, page_id: str) -> str:
    res = await session.execute(select(Page).where(Page.id == page_id))
    return res.scalar_one().freshness_status


async def test_a_page_for_a_deleted_file_is_tombstoned(session, tmp_path: Path):
    """The whole point: no diff involved, just a file that is not there."""
    repo_id = await _seed(session, tmp_path, "kept.py", "gone.py")
    _write(tmp_path, "kept.py")

    marked = await tombstone_absent_file_pages(session, repo_id, tmp_path)

    assert marked == ["file_page:gone.py"]
    assert await _status(session, "file_page:gone.py") == "tombstone"


async def test_a_page_for_a_file_still_on_disk_is_untouched(session, tmp_path: Path):
    repo_id = await _seed(session, tmp_path, "kept.py", "gone.py")
    _write(tmp_path, "kept.py")

    await tombstone_absent_file_pages(session, repo_id, tmp_path)

    assert await _status(session, "file_page:kept.py") == "fresh"


async def test_the_tombstone_claims_no_successor(session, tmp_path: Path):
    """Deleted, not renamed.

    Rename detection is the diff-driven sweep's job and it has evidence this
    one does not. Guessing a successor here would redirect a reader to a file
    that has nothing to do with the one they asked for.
    """
    repo_id = await _seed(session, tmp_path, "kept.py", "gone.py")
    _write(tmp_path, "kept.py")

    await tombstone_absent_file_pages(session, repo_id, tmp_path)

    res = await session.execute(select(Page).where(Page.id == "file_page:gone.py"))
    assert json.loads(res.scalar_one().metadata_json)["successor_paths"] == []


async def test_every_page_absent_is_refused_rather_than_obeyed(
    session, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """A wrong root reads exactly like a repository that lost every file.

    One of those readings wipes the wiki, and unlike a partial mistake it is
    not recoverable by re-indexing a file, so the sweep declines to act on
    it — at ERROR, which is the only level that survives ``repowise init``.
    """
    repo_id = await _seed(session, tmp_path, "a.py", "b.py", "c.py")
    # Nothing written to disk: as far as the sweep can see, all three are gone.

    with caplog.at_level("ERROR"):
        marked = await tombstone_absent_file_pages(session, repo_id, tmp_path)

    assert marked == []
    assert await _status(session, "file_page:a.py") == "fresh"
    assert "tombstone_sweep_refused" in caplog.text


async def test_only_file_pages_are_swept(session, tmp_path: Path):
    """A module page's target_path is a grouping, not a file that must exist.

    Several page types carry a target_path that never named a file — a
    clustering ordinal, a layer's curated id — and testing those against the
    filesystem would tombstone every one of them.
    """
    repo_id = await _seed(session, tmp_path, "community-7", page_type="module_page")
    await _seed(session, tmp_path, "kept.py", "gone.py")
    _write(tmp_path, "kept.py")

    marked = await tombstone_absent_file_pages(session, repo_id, tmp_path)

    assert marked == ["file_page:gone.py"]
    assert await _status(session, "module_page:community-7") == "fresh"


async def test_an_already_tombstoned_page_is_not_marked_again(session, tmp_path: Path):
    """The returned ids drive a full-text delete, so re-reporting one every
    run means re-deleting a row that left the index the first time."""
    repo_id = await _seed(session, tmp_path, "kept.py", "gone.py")
    _write(tmp_path, "kept.py")

    first = await tombstone_absent_file_pages(session, repo_id, tmp_path)
    second = await tombstone_absent_file_pages(session, repo_id, tmp_path)

    assert first == ["file_page:gone.py"]
    assert second == []


async def test_a_repository_with_no_file_pages_does_nothing(session, tmp_path: Path):
    repo = await upsert_repository(session, name="r", local_path=str(tmp_path))
    await session.commit()

    assert await tombstone_absent_file_pages(session, repo.id, tmp_path) == []
