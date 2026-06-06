"""Tests for the stale generated-page sweep.

Module/scc pages key on clustering ordinals and layer pages on display
names — identities that drift between runs. A full run's output is
authoritative for those page types: anything it did not reproduce is a
stranded duplicate from an earlier run and must be swept.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import select

from repowise.core.persistence.models import Page, PageVersion
from repowise.core.pipeline.persist import _sweep_stale_generated_pages
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


def _generated(page_type: str, target: str) -> SimpleNamespace:
    return SimpleNamespace(page_id=f"{page_type}:{target}", page_type=page_type)


async def test_sweep_removes_pages_absent_from_run(async_session):
    repo = await insert_repo(async_session)
    # Two module pages from a previous run; the new run reproduces only one
    # (under a fresh community ordinal) — the other is stale.
    async_session.add(_page_row(repo.id, "module_page", "community-155"))
    async_session.add(_page_row(repo.id, "module_page", "community-75"))
    async_session.add(
        PageVersion(
            page_id="module_page:community-155",
            repository_id=repo.id,
            version=1,
            page_type="module_page",
            title="old",
            content="old body",
            source_hash="x" * 64,
            model_name="mock",
            provider_name="mock",
            archived_at=datetime.now(UTC),
        )
    )
    await async_session.flush()

    swept = await _sweep_stale_generated_pages(
        async_session, repo.id, [_generated("module_page", "community-75")]
    )
    await async_session.commit()

    assert swept == ["module_page:community-155"]
    remaining = (
        (await async_session.execute(select(Page.id).where(Page.repository_id == repo.id)))
        .scalars()
        .all()
    )
    assert remaining == ["module_page:community-75"]
    # Versions of the swept page go with it (FK would block the delete).
    versions = (
        (
            await async_session.execute(
                select(PageVersion).where(PageVersion.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    assert versions == []


async def test_sweep_skips_types_the_run_did_not_produce(async_session):
    repo = await insert_repo(async_session)
    async_session.add(_page_row(repo.id, "module_page", "community-1"))
    async_session.add(_page_row(repo.id, "layer_page", "layer:Data"))
    await async_session.flush()

    # The run produced layer pages but no module pages (e.g. the module level
    # was skipped) — module pages must survive.
    swept = await _sweep_stale_generated_pages(
        async_session, repo.id, [_generated("layer_page", "layer:Data")]
    )
    await async_session.commit()

    assert swept == []
    remaining = (
        (await async_session.execute(select(Page.id).where(Page.repository_id == repo.id)))
        .scalars()
        .all()
    )
    assert set(remaining) == {"module_page:community-1", "layer_page:layer:Data"}


async def test_sweep_never_touches_unswept_page_types(async_session):
    repo = await insert_repo(async_session)
    # file_page ids are stable paths — never swept here even when absent
    # from the run's output.
    async_session.add(_page_row(repo.id, "file_page", "src/app.py"))
    async_session.add(_page_row(repo.id, "module_page", "community-9"))
    await async_session.flush()

    swept = await _sweep_stale_generated_pages(
        async_session, repo.id, [_generated("module_page", "community-10")]
    )
    await async_session.commit()

    assert swept == ["module_page:community-9"]
    remaining = (
        (await async_session.execute(select(Page.id).where(Page.repository_id == repo.id)))
        .scalars()
        .all()
    )
    assert "file_page:src/app.py" in remaining


async def test_sweep_noop_on_empty_run(async_session):
    repo = await insert_repo(async_session)
    async_session.add(_page_row(repo.id, "module_page", "community-1"))
    await async_session.flush()

    swept = await _sweep_stale_generated_pages(async_session, repo.id, [])
    assert swept == []
    swept = await _sweep_stale_generated_pages(async_session, repo.id, None)
    assert swept == []


async def test_authoritative_type_sweeps_when_zero_produced(async_session):
    """Regression for the live mini-taskq bug.

    A curated full run derived modules 1:1 with layers (every module
    ``wholeLayer``-skipped), so it emitted layer pages and ZERO module pages
    while still being authoritative for ``module_page``. The pre-curated
    community module page must be swept even though the run produced none.
    """
    repo = await insert_repo(async_session)
    async_session.add(_page_row(repo.id, "module_page", "community-0"))
    async_session.add(_page_row(repo.id, "layer_page", "layer:Data"))
    await async_session.flush()

    swept = await _sweep_stale_generated_pages(
        async_session,
        repo.id,
        [_generated("layer_page", "layer:Data")],
        {"module_page", "layer_page"},
    )
    await async_session.commit()

    assert swept == ["module_page:community-0"]
    remaining = (
        (await async_session.execute(select(Page.id).where(Page.repository_id == repo.id)))
        .scalars()
        .all()
    )
    assert set(remaining) == {"layer_page:layer:Data"}


async def test_degraded_run_does_not_wipe_layer_pages(async_session):
    """A community-fallback (degraded) run is authoritative for nothing.

    It produces module pages but no layer authority, so pre-existing curated
    layer pages it cannot reproduce must survive — degradation honesty.
    """
    repo = await insert_repo(async_session)
    async_session.add(_page_row(repo.id, "layer_page", "layer:Core"))
    async_session.add(_page_row(repo.id, "module_page", "community-1"))
    await async_session.flush()

    swept = await _sweep_stale_generated_pages(
        async_session,
        repo.id,
        [_generated("module_page", "community-2")],
        set(),  # degraded: no authority
    )
    await async_session.commit()

    # The stale community module page is swept (its type was produced), but the
    # curated layer page is untouched (not produced, not authoritative). The
    # produced ``community-2`` page is upserted elsewhere, not by the sweep, so
    # it is not among the seeded rows here.
    assert swept == ["module_page:community-1"]
    remaining = (
        (await async_session.execute(select(Page.id).where(Page.repository_id == repo.id)))
        .scalars()
        .all()
    )
    assert set(remaining) == {"layer_page:layer:Core"}


async def test_incremental_no_authority_sweeps_nothing(async_session):
    """Incremental no-KG run: generated_pages None + empty authority → no-op."""
    repo = await insert_repo(async_session)
    async_session.add(_page_row(repo.id, "module_page", "community-1"))
    async_session.add(_page_row(repo.id, "layer_page", "layer:Core"))
    await async_session.flush()

    swept = await _sweep_stale_generated_pages(async_session, repo.id, None, set())
    assert swept == []
    swept = await _sweep_stale_generated_pages(async_session, repo.id, None, None)
    assert swept == []
