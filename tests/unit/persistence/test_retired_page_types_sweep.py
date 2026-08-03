"""Tests for the retired-page-type sweep.

A retired page type has no live rows: nothing emits one and no failure mode
can make anything emit one. So unlike the other two sweeps, this one does not
ask what the run produced — which is what makes it safe on a scoped run, where
absence is normally no evidence at all.

The retirement tables in ``page_redirects`` are the source of truth. A type
that redirects but is never swept leaves an index serving a page the product
has replaced, which is the state this was written to clear.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from repowise.core.generation.page_redirects import (
    SUPERSEDED_TO_REPO_WIDE,
    SUPERSEDED_TYPES,
)
from repowise.core.persistence.models import Page, PageVersion
from repowise.core.pipeline.persist import sweep_retired_page_types
from tests.unit.persistence.helpers import insert_repo


def _page_row(repo_id: str, page_type: str, target: str) -> Page:
    now = datetime.now(UTC)
    return Page(
        id=f"{page_type}:{target}",
        repository_id=repo_id,
        page_type=page_type,
        title=target,
        content="body",
        target_path=target,
        source_hash="x" * 64,
        model_name="mock",
        provider_name="mock",
        created_at=now,
        updated_at=now,
    )


async def test_every_retired_type_is_swept(async_session):
    """Driven by the redirect tables, so a new retirement is covered for free."""
    repo = await insert_repo(async_session)
    retired = sorted(set(SUPERSEDED_TYPES) | set(SUPERSEDED_TO_REPO_WIDE))
    assert retired, "the redirect tables are empty; this sweep has nothing to do"
    for page_type in retired:
        async_session.add(_page_row(repo.id, page_type, f"leftover-{page_type}"))
    await async_session.flush()

    swept = await sweep_retired_page_types(async_session, repo.id)

    assert sorted(swept) == sorted(f"{t}:leftover-{t}" for t in retired)
    remaining = (await async_session.execute(select(Page.page_type))).scalars().all()
    assert not set(remaining) & set(retired)


async def test_live_page_types_are_untouched(async_session):
    """The sweep names types, not rows, so a live page must not be caught."""
    repo = await insert_repo(async_session)
    async_session.add(_page_row(repo.id, "layer_page", "layer:service"))
    async_session.add(_page_row(repo.id, "module_page", "src/ingest"))
    async_session.add(_page_row(repo.id, "file_page", "src/ingest/a.py"))
    async_session.add(_page_row(repo.id, "repo_overview", "demo"))
    await async_session.flush()

    await sweep_retired_page_types(async_session, repo.id)

    survivors = set((await async_session.execute(select(Page.id))).scalars().all())
    assert survivors == {
        "module_page:src/ingest",
        "file_page:src/ingest/a.py",
        "repo_overview:demo",
    }


async def test_versions_go_with_the_page(async_session):
    """FK enforcement requires it, and a retired id never returns to claim them."""
    repo = await insert_repo(async_session)
    async_session.add(_page_row(repo.id, "architecture_diagram", "demo"))
    async_session.add(
        PageVersion(
            page_id="architecture_diagram:demo",
            repository_id=repo.id,
            version=1,
            page_type="architecture_diagram",
            title="old",
            content="old body",
            source_hash="x" * 64,
            model_name="mock",
            provider_name="mock",
            archived_at=datetime.now(UTC),
        )
    )
    await async_session.flush()

    await sweep_retired_page_types(async_session, repo.id)

    left = (await async_session.execute(select(PageVersion.page_id))).scalars().all()
    assert left == []


async def test_other_repositories_are_not_touched(async_session):
    """The sweep is repo-scoped; a shared store must not lose a neighbour's rows."""
    mine = await insert_repo(async_session)
    theirs = await insert_repo(async_session, name="other", local_path="/tmp/other")
    async_session.add(_page_row(mine.id, "architecture_diagram", "mine"))
    async_session.add(_page_row(theirs.id, "architecture_diagram", "theirs"))
    await async_session.flush()

    swept = await sweep_retired_page_types(async_session, mine.id)

    assert swept == ["architecture_diagram:mine"]
    survivors = (await async_session.execute(select(Page.id))).scalars().all()
    assert survivors == ["architecture_diagram:theirs"]


async def test_a_clean_index_sweeps_nothing(async_session):
    repo = await insert_repo(async_session)
    async_session.add(_page_row(repo.id, "module_page", "src/ingest"))
    await async_session.flush()

    assert await sweep_retired_page_types(async_session, repo.id) == []
