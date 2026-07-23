"""Fields that must survive the idempotent-touch branch of a page upsert.

``_apply_page_upsert`` has three branches: insert, archive-then-update, and an
idempotent touch taken when content, prompt hash and model are all unchanged.
The touch branch deliberately refreshes only a few cheap fields so that
re-running a generation does not spawn a version snapshot per page.

That is right for content, and wrong for anything that describes where a page
sits rather than what it says. A page's display name and its position in the
tree are derived from the repo's structure, not from its own bytes: adding a
sibling file renumbers a page whose content did not change by a character. Such
a page takes the touch branch, so any field the branch does not assign is
frozen at whatever the first run wrote.

These tests pin the fields that must not freeze.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.crud import get_page, upsert_page
from tests.unit.persistence.helpers import insert_repo

_COMMON = {
    "page_type": "module_page",
    "content": "Body that does not change.",
    "target_path": "packages/core/ingestion",
    "source_hash": "h" * 64,
    "model_name": "mock",
    "provider_name": "mock",
}

_PAGE_ID = "module_page:packages/core/ingestion"


async def _upsert(session, **overrides):
    await upsert_page(
        session,
        page_id=_PAGE_ID,
        repository_id=overrides.pop("repository_id"),
        **{**_COMMON, **overrides},
    )


@pytest.fixture
async def repo_id(async_session):
    repo = await insert_repo(async_session)
    return repo.id


async def test_the_second_upsert_really_takes_the_touch_branch(async_session, repo_id):
    """Guards the rest of the file: if the branch changed, these stop testing it."""
    await _upsert(async_session, repository_id=repo_id, title="Ingestion Pipeline")
    await async_session.commit()
    await _upsert(async_session, repository_id=repo_id, title="Code Ingestion")
    await async_session.commit()

    page = await get_page(async_session, _PAGE_ID)
    # No version bump and no snapshot means the touch branch ran, not the
    # archive-then-update branch.
    assert page.version == 1


async def test_title_is_refreshed_when_only_the_title_changed(async_session, repo_id):
    """A rename must land.

    Page identity is anchored to structure so that renaming is free and
    non-destructive. That only holds if the new name is actually stored: a
    concept page keyed on its members can be re-titled while its body is
    byte-identical, and before this was fixed the store kept the first title
    forever.
    """
    await _upsert(async_session, repository_id=repo_id, title="Ingestion Pipeline")
    await async_session.commit()
    await _upsert(async_session, repository_id=repo_id, title="Code Ingestion")
    await async_session.commit()

    page = await get_page(async_session, _PAGE_ID)
    assert page.title == "Code Ingestion"


async def test_repeated_upsert_of_an_unchanged_page_is_still_a_no_op(async_session, repo_id):
    """The reason the touch branch exists must survive the fix."""
    await _upsert(async_session, repository_id=repo_id, title="Ingestion Pipeline")
    await async_session.commit()
    await _upsert(async_session, repository_id=repo_id, title="Ingestion Pipeline")
    await async_session.commit()

    page = await get_page(async_session, _PAGE_ID)
    assert page.version == 1
    assert page.title == "Ingestion Pipeline"


async def test_content_change_still_archives_a_version(async_session, repo_id):
    """The touch branch must not swallow a real edit."""
    await _upsert(async_session, repository_id=repo_id, title="Ingestion Pipeline")
    await async_session.commit()
    await _upsert(
        async_session,
        repository_id=repo_id,
        title="Ingestion Pipeline",
        content="A different body.",
    )
    await async_session.commit()

    page = await get_page(async_session, _PAGE_ID)
    assert page.version == 2
    assert page.content == "A different body."
