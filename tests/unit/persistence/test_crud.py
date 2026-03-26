"""Unit tests for the async CRUD layer.

All tests use an in-memory SQLite database.  The async_session fixture
yields an uncommitted session; tests must call await session.commit() when
they want to verify that changes persist across a query boundary.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.crud import (
    batch_upsert_graph_edges,
    batch_upsert_graph_nodes,
    batch_upsert_symbols,
    get_generation_job,
    get_page,
    get_page_versions,
    get_repository,
    get_repository_by_path,
    get_stale_pages,
    list_pages,
    mark_webhook_processed,
    store_webhook_event,
    update_job_status,
    upsert_generation_job,
    upsert_page,
    upsert_repository,
)
from tests.unit.persistence.helpers import insert_repo, make_page_kwargs


# ---------------------------------------------------------------------------
# Repository CRUD
# ---------------------------------------------------------------------------


async def test_upsert_repository_creates_new(async_session):
    repo = await upsert_repository(async_session, name="demo", local_path="/tmp/demo")
    await async_session.commit()
    assert repo.id is not None
    assert repo.name == "demo"
    assert repo.local_path == "/tmp/demo"


async def test_upsert_repository_updates_existing(async_session):
    repo = await upsert_repository(async_session, name="v1", local_path="/tmp/r")
    await async_session.commit()

    repo2 = await upsert_repository(async_session, name="v2", local_path="/tmp/r")
    await async_session.commit()

    assert repo.id == repo2.id
    assert repo2.name == "v2"


async def test_get_repository_returns_none_for_missing(async_session):
    result = await get_repository(async_session, "nonexistent")
    assert result is None


async def test_get_repository_by_path_returns_none_for_missing(async_session):
    result = await get_repository_by_path(async_session, "/not/there")
    assert result is None


async def test_get_repository_returns_inserted(async_session):
    repo = await insert_repo(async_session, name="find-me", local_path="/tmp/find-me")
    found = await get_repository(async_session, repo.id)
    assert found is not None
    assert found.id == repo.id


# ---------------------------------------------------------------------------
# GenerationJob CRUD
# ---------------------------------------------------------------------------


async def test_upsert_generation_job_creates(async_session):
    repo = await insert_repo(async_session)
    job = await upsert_generation_job(
        async_session,
        repository_id=repo.id,
        provider_name="mock",
        model_name="mock-v1",
        total_pages=10,
    )
    await async_session.commit()
    assert job.status == "pending"
    assert job.total_pages == 10
    assert job.started_at is None


async def test_update_job_status_to_running(async_session):
    repo = await insert_repo(async_session)
    job = await upsert_generation_job(async_session, repository_id=repo.id)
    await async_session.commit()

    updated = await update_job_status(async_session, job.id, "running")
    await async_session.commit()

    assert updated.status == "running"
    assert updated.started_at is not None


async def test_update_job_status_to_completed(async_session):
    repo = await insert_repo(async_session)
    job = await upsert_generation_job(async_session, repository_id=repo.id)
    await async_session.commit()

    updated = await update_job_status(
        async_session, job.id, "completed", completed_pages=5
    )
    await async_session.commit()

    assert updated.status == "completed"
    assert updated.finished_at is not None
    assert updated.completed_pages == 5


async def test_update_job_status_invalid_raises(async_session):
    repo = await insert_repo(async_session)
    job = await upsert_generation_job(async_session, repository_id=repo.id)
    await async_session.commit()

    with pytest.raises(ValueError, match="Unknown job status"):
        await update_job_status(async_session, job.id, "exploded")


async def test_update_job_status_missing_job_raises(async_session):
    with pytest.raises(LookupError):
        await update_job_status(async_session, "does-not-exist", "running")


async def test_get_generation_job_returns_none_for_missing(async_session):
    result = await get_generation_job(async_session, "missing")
    assert result is None


# ---------------------------------------------------------------------------
# Page CRUD (versioning)
# ---------------------------------------------------------------------------


async def test_upsert_page_creates_version_1(async_session):
    repo = await insert_repo(async_session)
    page = await upsert_page(async_session, **make_page_kwargs(repo.id))
    await async_session.commit()

    assert page.version == 1
    assert page.id == "file_page:src/main.py"


async def test_upsert_page_creates_version_on_second_upsert(async_session):
    repo = await insert_repo(async_session)
    kwargs = make_page_kwargs(repo.id)
    await upsert_page(async_session, **kwargs)
    await async_session.commit()

    # Second upsert with different content
    kwargs2 = dict(kwargs, content="# Updated content", source_hash="def456")
    await upsert_page(async_session, **kwargs2)
    await async_session.commit()

    versions = await get_page_versions(async_session, "file_page:src/main.py")
    assert len(versions) == 1
    assert versions[0].version == 1
    assert versions[0].content == "# Main module\n\nEntry point for the application."


async def test_upsert_page_increments_version_field(async_session):
    repo = await insert_repo(async_session)
    kwargs = make_page_kwargs(repo.id)
    await upsert_page(async_session, **kwargs)
    await async_session.commit()

    await upsert_page(async_session, **dict(kwargs, source_hash="v2"))
    await async_session.commit()

    page = await get_page(async_session, "file_page:src/main.py")
    assert page is not None
    assert page.version == 2


async def test_upsert_page_preserves_created_at(async_session):
    from datetime import datetime, timezone

    repo = await insert_repo(async_session)
    original_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    kwargs = make_page_kwargs(repo.id, created_at=original_time)
    await upsert_page(async_session, **kwargs)
    await async_session.commit()

    await upsert_page(async_session, **dict(kwargs, source_hash="v2"))
    await async_session.commit()

    page = await get_page(async_session, "file_page:src/main.py")
    assert page is not None
    # created_at must not change on update.
    # SQLite may strip timezone info on round-trip, so compare naive UTC.
    stored_naive = page.created_at.replace(tzinfo=None) if page.created_at.tzinfo else page.created_at
    original_naive = original_time.replace(tzinfo=None)
    assert stored_naive == original_naive


async def test_get_page_returns_none_for_missing(async_session):
    assert await get_page(async_session, "missing:page") is None


async def test_list_pages_returns_all_for_repo(async_session):
    repo = await insert_repo(async_session)
    await upsert_page(async_session, **make_page_kwargs(repo.id, page_id="file_page:a.py", target_path="a.py"))
    await upsert_page(async_session, **make_page_kwargs(repo.id, page_id="file_page:b.py", target_path="b.py"))
    await async_session.commit()

    pages = await list_pages(async_session, repo.id)
    assert len(pages) == 2


async def test_list_pages_filters_by_page_type(async_session):
    repo = await insert_repo(async_session)
    await upsert_page(async_session, **make_page_kwargs(repo.id, page_id="file_page:a.py", target_path="a.py"))
    await upsert_page(
        async_session,
        **make_page_kwargs(
            repo.id,
            page_id="repo_overview:.",
            target_path=".",
            page_type="repo_overview",
        ),
    )
    await async_session.commit()

    file_pages = await list_pages(async_session, repo.id, page_type="file_page")
    assert len(file_pages) == 1
    assert file_pages[0].page_type == "file_page"


async def test_list_pages_pagination(async_session):
    repo = await insert_repo(async_session)
    for i in range(5):
        await upsert_page(
            async_session,
            **make_page_kwargs(
                repo.id,
                page_id=f"file_page:src/{i}.py",
                target_path=f"src/{i}.py",
            ),
        )
    await async_session.commit()

    first_two = await list_pages(async_session, repo.id, limit=2, offset=0)
    next_two = await list_pages(async_session, repo.id, limit=2, offset=2)
    assert len(first_two) == 2
    assert len(next_two) == 2
    # No overlap
    ids_first = {p.id for p in first_two}
    ids_next = {p.id for p in next_two}
    assert ids_first.isdisjoint(ids_next)


async def test_get_page_versions_returns_ordered_desc(async_session):
    repo = await insert_repo(async_session)
    kwargs = make_page_kwargs(repo.id)
    await upsert_page(async_session, **kwargs)
    await async_session.commit()
    await upsert_page(async_session, **dict(kwargs, source_hash="v2"))
    await async_session.commit()
    await upsert_page(async_session, **dict(kwargs, source_hash="v3"))
    await async_session.commit()

    versions = await get_page_versions(async_session, kwargs["page_id"])
    assert len(versions) == 2
    assert versions[0].version > versions[1].version  # descending


async def test_get_stale_pages_returns_only_stale(async_session):
    repo = await insert_repo(async_session)
    await upsert_page(
        async_session,
        **make_page_kwargs(repo.id, page_id="file_page:fresh.py", target_path="fresh.py"),
    )
    await upsert_page(
        async_session,
        **make_page_kwargs(
            repo.id,
            page_id="file_page:stale.py",
            target_path="stale.py",
            freshness_status="stale",
        ),
    )
    await upsert_page(
        async_session,
        **make_page_kwargs(
            repo.id,
            page_id="file_page:expired.py",
            target_path="expired.py",
            freshness_status="expired",
        ),
    )
    await async_session.commit()

    stale = await get_stale_pages(async_session, repo.id)
    assert len(stale) == 2
    statuses = {p.freshness_status for p in stale}
    assert statuses == {"stale", "expired"}


# ---------------------------------------------------------------------------
# Graph CRUD (batch)
# ---------------------------------------------------------------------------


async def test_batch_upsert_graph_nodes_inserts(async_session):
    from sqlalchemy import select
    from repowise.core.persistence.models import GraphNode

    repo = await insert_repo(async_session)
    nodes = [
        {"node_id": "src/a.py", "language": "python", "pagerank": 0.5},
        {"node_id": "src/b.py", "language": "python", "pagerank": 0.3},
    ]
    await batch_upsert_graph_nodes(async_session, repo.id, nodes)
    await async_session.commit()

    result = await async_session.execute(
        select(GraphNode).where(GraphNode.repository_id == repo.id)
    )
    saved = result.scalars().all()
    assert len(saved) == 2


async def test_batch_upsert_graph_nodes_updates_existing(async_session):
    from sqlalchemy import select
    from repowise.core.persistence.models import GraphNode

    repo = await insert_repo(async_session)
    await batch_upsert_graph_nodes(
        async_session, repo.id, [{"node_id": "src/a.py", "pagerank": 0.1}]
    )
    await async_session.commit()

    await batch_upsert_graph_nodes(
        async_session, repo.id, [{"node_id": "src/a.py", "pagerank": 0.9}]
    )
    await async_session.commit()

    result = await async_session.execute(
        select(GraphNode).where(GraphNode.repository_id == repo.id)
    )
    saved = result.scalars().all()
    assert len(saved) == 1
    assert saved[0].pagerank == pytest.approx(0.9)


async def test_batch_upsert_graph_edges_inserts(async_session):
    from sqlalchemy import select
    from repowise.core.persistence.models import GraphEdge

    repo = await insert_repo(async_session)
    edges = [
        {"source_node_id": "a.py", "target_node_id": "b.py"},
        {"source_node_id": "b.py", "target_node_id": "c.py"},
    ]
    await batch_upsert_graph_edges(async_session, repo.id, edges)
    await async_session.commit()

    result = await async_session.execute(
        select(GraphEdge).where(GraphEdge.repository_id == repo.id)
    )
    saved = result.scalars().all()
    assert len(saved) == 2


# ---------------------------------------------------------------------------
# Symbol CRUD (batch)
# ---------------------------------------------------------------------------


async def test_batch_upsert_symbols_inserts(async_session):
    from dataclasses import dataclass
    from sqlalchemy import select
    from repowise.core.persistence.models import WikiSymbol

    @dataclass
    class FakeSym:
        name: str
        kind: str = "function"
        file_path: str = "src/a.py"
        qualified_name: str = ""
        signature: str = ""
        start_line: int = 1
        end_line: int = 5
        docstring: str | None = None
        visibility: str = "public"
        is_async: bool = False
        complexity_estimate: int = 0
        language: str = "python"
        parent_name: str | None = None

        @property
        def id(self):
            return f"{self.file_path}::{self.name}"

    repo = await insert_repo(async_session)
    syms = [FakeSym("foo"), FakeSym("bar")]
    await batch_upsert_symbols(async_session, repo.id, syms)
    await async_session.commit()

    result = await async_session.execute(
        select(WikiSymbol).where(WikiSymbol.repository_id == repo.id)
    )
    saved = result.scalars().all()
    assert len(saved) == 2
    names = {s.name for s in saved}
    assert names == {"foo", "bar"}


# ---------------------------------------------------------------------------
# WebhookEvent CRUD
# ---------------------------------------------------------------------------


async def test_store_webhook_event_creates(async_session):
    event = await store_webhook_event(
        async_session,
        provider="github",
        event_type="push",
        payload={"ref": "refs/heads/main"},
    )
    await async_session.commit()

    assert event.provider == "github"
    assert event.processed is False
    assert event.repository_id is None


async def test_mark_webhook_processed(async_session):
    repo = await insert_repo(async_session)
    event = await store_webhook_event(
        async_session,
        provider="github",
        event_type="push",
        payload={},
        repository_id=repo.id,
    )
    await async_session.commit()

    await mark_webhook_processed(async_session, event.id)
    await async_session.commit()

    from repowise.core.persistence.models import WebhookEvent
    from sqlalchemy import select

    result = await async_session.execute(
        select(WebhookEvent).where(WebhookEvent.id == event.id)
    )
    updated = result.scalar_one()
    assert updated.processed is True


async def test_mark_webhook_processed_missing_raises(async_session):
    with pytest.raises(LookupError):
        await mark_webhook_processed(async_session, "ghost-id")
