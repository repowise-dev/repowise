"""The update-path sweep (backlog B19).

A structurally-keyed page's id is its ``target_path``, and a page defined by the
files it covers can legitimately change which directory names it when its
membership changes. On a full index the existing sweep retires the old row. On
``repowise update`` nothing did, because the full sweep deletes every row of a
page type the run did not reproduce — correct for a full index, and on a scoped
run that regenerates one page out of fifty it would delete the other forty-nine.

So the rule here is different and the tests that matter are the ones proving it
is *narrow*: a page this run did not touch must survive, and so must a page
whose generation failed.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from repowise.core.persistence.models import Page, Repository
from repowise.core.pipeline.persist import sweep_superseded_generated_pages


@pytest.fixture
async def session(async_session):
    return async_session


async def _new_repo(session, name: str) -> str:
    repo_id = uuid.uuid4().hex[:32]
    session.add(
        Repository(id=repo_id, name=name, local_path=f"/tmp/{name}", url="")
    )
    await session.flush()
    return repo_id


@pytest.fixture
async def repo_id(session):
    return await _new_repo(session, "primary")


@pytest.fixture
async def other_repo_id(session):
    return await _new_repo(session, "other")


_NOW = datetime.now(UTC)


class _Generated:
    def __init__(self, page_id: str, page_type: str, members: list[str]):
        self.page_id = page_id
        self.page_type = page_type
        self.metadata = {"file_paths": list(members)}


async def _add(session, repo_id: str, page_id: str, page_type: str, members):
    session.add(
        Page(
            id=page_id,
            repository_id=repo_id,
            page_type=page_type,
            target_path=page_id.split(":", 1)[1],
            title=page_id,
            content="body",
            source_hash="h" * 64,
            model_name="m",
            provider_name="template",
            metadata_json=json.dumps({"file_paths": list(members)}),
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    await session.flush()


async def _ids(session, repo_id: str) -> set[str]:
    rows = await session.execute(select(Page.id).where(Page.repository_id == repo_id))
    return set(rows.scalars())


@pytest.mark.asyncio
async def test_a_row_whose_coverage_moved_to_a_new_page_is_retired(
    session, repo_id
):
    """The bug: the same page under a new id, with the old row left behind."""
    members = ["src/a.py", "src/b.py"]
    await _add(session, repo_id, "module_page:src/old", "module_page", members)
    swept = await sweep_superseded_generated_pages(
        session,
        repo_id,
        [_Generated("module_page:src/new", "module_page", members)],
    )
    assert swept == ["module_page:src/old"]
    assert "module_page:src/old" not in await _ids(session, repo_id)


@pytest.mark.asyncio
async def test_an_untouched_page_survives(session, repo_id):
    """The reason the full sweep cannot be reused here."""
    await _add(session, repo_id, "module_page:src/keep", "module_page", ["src/z.py"])
    await _add(session, repo_id, "module_page:src/old", "module_page", ["src/a.py"])
    swept = await sweep_superseded_generated_pages(
        session,
        repo_id,
        [_Generated("module_page:src/new", "module_page", ["src/a.py"])],
    )
    assert swept == ["module_page:src/old"]
    assert "module_page:src/keep" in await _ids(session, repo_id)


@pytest.mark.asyncio
async def test_a_page_whose_generation_failed_is_not_deleted(session, repo_id):
    """The failure mode worse than the bug being fixed.

    A page the run meant to regenerate but could not still covers files no
    produced page claims, so it is left alone. Deleting a page because its
    generation errored would lose content rather than a duplicate.
    """
    await _add(session, repo_id, "module_page:src/failed", "module_page", ["src/f.py"])
    swept = await sweep_superseded_generated_pages(
        session,
        repo_id,
        [_Generated("module_page:src/ok", "module_page", ["src/a.py"])],
    )
    assert swept == []
    assert "module_page:src/failed" in await _ids(session, repo_id)


@pytest.mark.asyncio
async def test_partial_overlap_does_not_retire_a_row(session, repo_id):
    """Supersession means *all* of it, not some of it."""
    await _add(
        session, repo_id, "module_page:src/old", "module_page", ["src/a.py", "src/b.py"]
    )
    swept = await sweep_superseded_generated_pages(
        session,
        repo_id,
        [_Generated("module_page:src/new", "module_page", ["src/a.py"])],
    )
    assert swept == []


@pytest.mark.asyncio
async def test_a_row_with_no_recorded_membership_is_left_alone(session, repo_id):
    """Pre-membership rows (backlog B20) cannot be proven superseded."""
    await _add(session, repo_id, "module_page:src/old", "module_page", [])
    swept = await sweep_superseded_generated_pages(
        session,
        repo_id,
        [_Generated("module_page:src/new", "module_page", ["src/a.py"])],
    )
    assert swept == []
    assert "module_page:src/old" in await _ids(session, repo_id)


@pytest.mark.asyncio
async def test_only_structurally_keyed_types_are_swept(session, repo_id):
    """A file page's id is its path; it has no identity that can move."""
    await _add(session, repo_id, "file_page:src/a.py", "file_page", ["src/a.py"])
    swept = await sweep_superseded_generated_pages(
        session,
        repo_id,
        [_Generated("file_page:src/b.py", "file_page", ["src/a.py"])],
    )
    assert swept == []
    assert "file_page:src/a.py" in await _ids(session, repo_id)


@pytest.mark.asyncio
async def test_a_run_that_produced_nothing_retires_nothing(session, repo_id):
    await _add(session, repo_id, "module_page:src/old", "module_page", ["src/a.py"])
    assert await sweep_superseded_generated_pages(session, repo_id, []) == []
    assert await sweep_superseded_generated_pages(session, repo_id, None) == []
    assert "module_page:src/old" in await _ids(session, repo_id)


@pytest.mark.asyncio
async def test_a_different_repository_is_untouched(session, repo_id, other_repo_id):
    await _add(session, other_repo_id, "module_page:src/old", "module_page", ["src/a.py"])
    swept = await sweep_superseded_generated_pages(
        session,
        repo_id,
        [_Generated("module_page:src/new", "module_page", ["src/a.py"])],
    )
    assert swept == []
    assert "module_page:src/old" in await _ids(session, other_repo_id)
