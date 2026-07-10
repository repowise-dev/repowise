"""Decay marking for weakly-affected pages (ChangeDetector.decay_only).

The detector computed decay_only on every update but nothing ever persisted
it, so pages hit by the cascade beyond the regeneration budget stayed
``fresh`` forever. ``mark_stale_pages`` closes that: coverage and
``get_stale_pages`` must reflect the decay, without downgrading tombstones
or touching unrelated pages.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.crud import get_stale_pages, upsert_page
from repowise.core.pipeline.persist import mark_stale_pages
from tests.unit.persistence.helpers import insert_repo, make_page_kwargs


async def _seed_page(session, repo_id, path, **overrides):
    kwargs = make_page_kwargs(
        repo_id,
        page_id=f"file_page:{path}",
        target_path=path,
        title=path,
        **overrides,
    )
    page = await upsert_page(session, **kwargs)
    await session.commit()
    return page


@pytest.mark.asyncio
async def test_fresh_pages_decay_to_stale(async_session):
    repo = await insert_repo(async_session)
    await _seed_page(async_session, repo.id, "src/a.py")
    await _seed_page(async_session, repo.id, "src/b.py")
    untouched = await _seed_page(async_session, repo.id, "src/c.py")

    marked = await mark_stale_pages(async_session, repo.id, ["src/a.py", "src/b.py"])
    await async_session.commit()

    assert marked == 2
    stale = await get_stale_pages(async_session, repo.id)
    assert sorted(p.target_path for p in stale) == ["src/a.py", "src/b.py"]
    await async_session.refresh(untouched)
    assert untouched.freshness_status == "fresh"


@pytest.mark.asyncio
async def test_tombstone_is_not_downgraded(async_session):
    repo = await insert_repo(async_session)
    page = await _seed_page(async_session, repo.id, "src/dead.py", freshness_status="tombstone")

    marked = await mark_stale_pages(async_session, repo.id, ["src/dead.py"])
    await async_session.commit()

    assert marked == 0
    await async_session.refresh(page)
    assert page.freshness_status == "tombstone"


@pytest.mark.asyncio
async def test_missing_pages_and_empty_input_are_noops(async_session):
    repo = await insert_repo(async_session)

    assert await mark_stale_pages(async_session, repo.id, []) == 0
    assert await mark_stale_pages(async_session, repo.id, ["src/never_indexed.py"]) == 0
