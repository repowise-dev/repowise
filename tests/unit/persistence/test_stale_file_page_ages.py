"""``get_stale_file_page_ages`` maps stale/expired file pages to their age.

The cascade-budget ordering in ChangeDetector.get_affected_pages consumes this
so a constrained docs run bubbles the oldest stale pages to the top instead of
reordering purely by importance (issues #847 / #851). These tests pin the
contract: which rows count, what "age" means, and what comes back when nothing
is stale.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.crud import (
    get_stale_file_page_ages,
    upsert_page,
    upsert_repository,
)
from repowise.core.persistence.database import init_db


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


async def _seed(session, *paths: str) -> str:
    """One file page per path (fresh unless noted). Returns the repo id."""
    repo = await upsert_repository(session, name="r", local_path="/tmp/r")
    await session.commit()
    for path in paths:
        await upsert_page(
            session,
            page_id=f"file_page:{path}",
            repository_id=repo.id,
            page_type="file_page",
            title=f"File: {path}",
            content=f"# Overview\n\nWhat {path} does.",
            summary=f"What {path} does.",
            target_path=path,
            source_hash=f"hash:{path}",
            model_name="test",
            provider_name="template",
        )
    await session.commit()
    return repo.id


async def _seed_stale(session, repo_id: str, path: str) -> None:
    """Re-upsert one page as stale/expired so its row is a stale file page."""
    await upsert_page(
        session,
        page_id=f"file_page:{path}",
        repository_id=repo_id,
        page_type="file_page",
        title=f"File: {path}",
        content=f"# Overview\n\nWhat {path} does.",
        summary=f"What {path} does.",
        target_path=path,
        source_hash=f"hash:{path}",
        model_name="test",
        provider_name="provider",
        freshness_status="stale",
    )
    await session.commit()


async def test_empty_when_nothing_stale(session) -> None:
    repo_id = await _seed(session, "a.py")
    ages = await get_stale_file_page_ages(session, repo_id)
    assert ages == {}


async def test_stale_file_pages_are_returned(session) -> None:
    repo_id = await _seed(session, "a.py", "b.py")
    await _seed_stale(session, repo_id, "b.py")
    ages = await get_stale_file_page_ages(session, repo_id)
    assert "b.py" in ages
    assert "a.py" not in ages  # fresh page is excluded
    assert ages["b.py"] >= 0.0


async def test_non_file_pages_are_ignored(session) -> None:
    repo = await upsert_repository(session, name="r2", local_path="/tmp/r2")
    await session.commit()
    # A stale non-file page (module_page) must not appear in the file-path map.
    await upsert_page(
        session,
        page_id="module_page:core",
        repository_id=repo.id,
        page_type="module_page",
        title="Core",
        content="body",
        summary="sum",
        target_path="core",
        source_hash="h",
        model_name="m",
        provider_name="p",
        freshness_status="stale",
    )
    await session.commit()
    ages = await get_stale_file_page_ages(session, repo.id)
    assert ages == {}


async def test_other_repository_is_isolated(session) -> None:
    repo_id = await _seed(session, "a.py")
    await _seed_stale(session, repo_id, "a.py")
    other = await upsert_repository(session, name="other", local_path="/tmp/other")
    await session.commit()
    ages = await get_stale_file_page_ages(session, other.id)
    assert ages == {}
