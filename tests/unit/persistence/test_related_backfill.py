"""Unit tests for the related-pages metadata backfill."""

from __future__ import annotations

import json

from sqlalchemy import select

from repowise.core.persistence.crud import backfill_related_pages, upsert_page
from repowise.core.persistence.models import Page
from tests.unit.persistence.helpers import insert_repo, make_page_kwargs


async def _insert_page(session, repo_id: str, path: str, **overrides):
    return await upsert_page(
        session,
        **make_page_kwargs(
            repo_id,
            page_id=f"file_page:{path}",
            target_path=path,
            title=path,
            **overrides,
        ),
    )


async def _related_of(session, page_id: str) -> list[dict] | None:
    row = (await session.execute(select(Page).where(Page.id == page_id))).scalars().one()
    return json.loads(row.metadata_json or "{}").get("related_pages")


async def test_backfill_heals_pages_without_related_metadata(async_session):
    """Pre-feature pages get related_pages from one backfill run."""
    repo = await insert_repo(async_session)
    await _insert_page(async_session, repo.id, "a.py")
    await _insert_page(async_session, repo.id, "b.py")

    changed = await backfill_related_pages(async_session, repo.id, import_edges=[("a.py", "b.py")])

    assert changed == 2  # a gets imports, b gets imported-by
    rel = await _related_of(async_session, "file_page:a.py")
    assert [(r["reason"], r["target_page_id"]) for r in rel] == [("imports", "file_page:b.py")]


async def test_backfill_preserves_same_module_entries(async_session):
    """Recomputing without module groups keeps prior same-module entries."""
    repo = await insert_repo(async_session)
    await _insert_page(
        async_session,
        repo.id,
        "a.py",
        metadata={
            "related_pages": [
                {
                    "target_page_id": "file_page:m.py",
                    "title": "m.py",
                    "reason": "same-module",
                    "weight": 0.0,
                }
            ]
        },
    )
    await _insert_page(async_session, repo.id, "b.py")
    await _insert_page(async_session, repo.id, "m.py")

    await backfill_related_pages(async_session, repo.id, import_edges=[("a.py", "b.py")])

    rel = await _related_of(async_session, "file_page:a.py")
    reasons = {(r["reason"], r["target_page_id"]) for r in rel}
    assert ("imports", "file_page:b.py") in reasons
    assert ("same-module", "file_page:m.py") in reasons


async def test_backfill_skips_current_run_pages(async_session):
    """Pages in skip_page_ids keep their metadata untouched."""
    repo = await insert_repo(async_session)
    await _insert_page(async_session, repo.id, "a.py")
    await _insert_page(async_session, repo.id, "b.py")

    changed = await backfill_related_pages(
        async_session,
        repo.id,
        import_edges=[("a.py", "b.py")],
        skip_page_ids={"file_page:a.py"},
    )

    assert changed == 1  # only b.py (imported-by)
    assert await _related_of(async_session, "file_page:a.py") is None


async def test_backfill_idempotent(async_session):
    """A second identical run reports zero changed rows."""
    repo = await insert_repo(async_session)
    await _insert_page(async_session, repo.id, "a.py")
    await _insert_page(async_session, repo.id, "b.py")
    edges = [("a.py", "b.py")]

    first = await backfill_related_pages(async_session, repo.id, import_edges=edges)
    second = await backfill_related_pages(async_session, repo.id, import_edges=edges)

    assert first == 2
    assert second == 0


async def test_backfill_skips_tombstoned_pages(async_session):
    repo = await insert_repo(async_session)
    await _insert_page(async_session, repo.id, "a.py")
    await _insert_page(async_session, repo.id, "gone.py", freshness_status="tombstone")

    changed = await backfill_related_pages(
        async_session, repo.id, import_edges=[("a.py", "gone.py")]
    )

    # gone.py is excluded both as a source row and from the resolution
    # index, so a.py ends up with an empty list and gone.py stays untouched.
    assert changed == 1
    assert await _related_of(async_session, "file_page:a.py") == []
    assert await _related_of(async_session, "file_page:gone.py") is None
