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


async def test_hierarchy_fields_are_refreshed_when_only_they_changed(async_session, repo_id):
    """Where a page sits can change while what it says does not.

    Adding a sibling renumbers this page without touching a byte of its
    content, so the update arrives on the touch branch. A hierarchy field the
    branch does not assign would stay at the first run's value and the tree
    would quietly describe a repo that no longer exists.
    """
    await _upsert(
        async_session,
        repository_id=repo_id,
        title="Ingestion Pipeline",
        parent_page_id="layer_page:layer:service",
        display_order=3,
        section_number="2.1",
        structural_key="grp-aaaaaaaaaaaa",
    )
    await async_session.commit()
    await _upsert(
        async_session,
        repository_id=repo_id,
        title="Ingestion Pipeline",
        parent_page_id="layer_page:layer:ingest",
        display_order=5,
        section_number="3.2.1",
        structural_key="grp-bbbbbbbbbbbb",
    )
    await async_session.commit()

    page = await get_page(async_session, _PAGE_ID)
    assert page.version == 1, "must have taken the touch branch"
    assert page.parent_page_id == "layer_page:layer:ingest"
    assert page.display_order == 5
    assert page.section_number == "3.2.1"
    assert page.structural_key == "grp-bbbbbbbbbbbb"


async def test_hierarchy_fields_default_for_a_page_that_has_no_place_yet(
    async_session, repo_id
):
    """Every existing page predates these columns, so the unset case is normal."""
    await _upsert(async_session, repository_id=repo_id, title="Ingestion Pipeline")
    await async_session.commit()

    page = await get_page(async_session, _PAGE_ID)
    assert page.parent_page_id is None
    assert page.section_number is None
    assert page.structural_key is None
    assert page.display_order == 0


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
