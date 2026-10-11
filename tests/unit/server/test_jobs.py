"""Tests for /api/jobs endpoints."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session, init_db
from repowise.core.persistence.models import GenerationJob
from repowise.server.app import reset_workspace_stale_jobs
from repowise.server.routers.jobs import _created_sort_key
from tests.unit.server.conftest import create_test_repo


@pytest.mark.asyncio
async def test_list_jobs_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/jobs")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_jobs_with_data(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)

    async with get_session(app.state.session_factory) as session:
        await crud.upsert_generation_job(
            session,
            repository_id=repo["id"],
            status="running",
            provider_name="mock",
            model_name="mock-model",
            total_pages=10,
        )

    resp = await client.get("/api/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status"] == "running"
    assert data[0]["total_pages"] == 10


@pytest.mark.asyncio
async def test_list_jobs_filter_by_repo(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)

    async with get_session(app.state.session_factory) as session:
        await crud.upsert_generation_job(
            session,
            repository_id=repo["id"],
            status="completed",
        )

    # Filter by repo_id
    resp = await client.get("/api/jobs", params={"repo_id": repo["id"]})
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # Filter by nonexistent repo_id
    resp = await client.get("/api/jobs", params={"repo_id": "nonexistent"})
    assert resp.status_code == 200
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_list_jobs_filter_by_workspace_repo(client: AsyncClient, app) -> None:
    """Workspace-mode job listing must query the repo's own wiki.db."""
    ws_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(ws_engine)
    ws_factory = async_sessionmaker(ws_engine, expire_on_commit=False, class_=AsyncSession)

    try:
        async with get_session(ws_factory) as session:
            repo = await crud.upsert_repository(
                session,
                name="workspace-repo",
                local_path="/tmp/workspace-repo",
            )
            await crud.upsert_generation_job(
                session,
                repository_id=repo.id,
                status="running",
                total_pages=12,
            )
            repo_id = repo.id

        app.state.workspace_sessions = {repo_id: ws_factory}

        resp = await client.get("/api/jobs", params={"repo_id": repo_id})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["repository_id"] == repo_id
        assert data[0]["status"] == "running"
        assert data[0]["total_pages"] == 12
    finally:
        await ws_engine.dispose()


@pytest.mark.asyncio
async def test_reset_workspace_stale_jobs_marks_secondary_db_jobs_failed() -> None:
    """Interrupted workspace jobs must not survive as running after restart."""
    ws_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(ws_engine)
    ws_factory = async_sessionmaker(ws_engine, expire_on_commit=False, class_=AsyncSession)

    try:
        async with get_session(ws_factory) as session:
            repo = await crud.upsert_repository(
                session,
                name="workspace-repo",
                local_path="/tmp/workspace-repo",
            )
            running_job = await crud.upsert_generation_job(
                session,
                repository_id=repo.id,
                status="running",
                total_pages=12,
            )
            completed_job = await crud.upsert_generation_job(
                session,
                repository_id=repo.id,
                status="completed",
                total_pages=1,
            )

        reset_count = await reset_workspace_stale_jobs(
            SimpleNamespace(workspace_sessions={repo.id: ws_factory})
        )

        async with get_session(ws_factory) as session:
            result = await session.execute(
                select(GenerationJob).where(
                    GenerationJob.id.in_([running_job.id, completed_job.id])
                )
            )
            jobs = {job.id: job for job in result.scalars().all()}

        assert reset_count == 1
        assert jobs[running_job.id].status == "failed"
        assert jobs[running_job.id].finished_at is not None
        assert jobs[running_job.id].error_message == "Server restarted; job interrupted"
        assert jobs[completed_job.id].status == "completed"
    finally:
        await ws_engine.dispose()


@pytest.mark.asyncio
async def test_get_job_by_id(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)

    async with get_session(app.state.session_factory) as session:
        job = await crud.upsert_generation_job(
            session,
            repository_id=repo["id"],
            status="pending",
        )
        job_id = job.id

    resp = await client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == job_id
    assert resp.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_get_job_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/jobs/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_job_stream_completed(client: AsyncClient, app) -> None:
    """SSE stream should emit progress and done events for a completed job."""
    repo = await create_test_repo(client)

    async with get_session(app.state.session_factory) as session:
        job = await crud.upsert_generation_job(
            session,
            repository_id=repo["id"],
            status="completed",
            total_pages=5,
        )
        await crud.update_job_status(session, job.id, "completed", completed_pages=5)
        job_id = job.id

    resp = await client.get(f"/api/jobs/{job_id}/stream")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    # For a completed job, the stream should contain 'done' event
    assert "event: done" in resp.text or "event: progress" in resp.text


def test_created_sort_key_handles_mixed_tz_awareness() -> None:
    """Jobs merged from several repo DBs mix naive and aware ``created_at``.

    In workspace mode the per-repo SQLite databases hand back a mix of
    timezone-aware and naive timestamps; sorting them directly raised
    ``TypeError: can't compare offset-naive and offset-aware datetimes`` and
    500'd ``GET /api/jobs``. The sort key must coerce them onto common ground.
    """
    from datetime import UTC, datetime

    aware_new = SimpleNamespace(created_at=datetime(2026, 1, 2, tzinfo=UTC))
    naive_newest = SimpleNamespace(created_at=datetime(2026, 1, 3))  # naive, newest
    aware_old = SimpleNamespace(created_at=datetime(2026, 1, 1, tzinfo=UTC))
    missing = SimpleNamespace(created_at=None)

    jobs = [aware_old, missing, naive_newest, aware_new]
    # Must not raise despite the naive/aware mix.
    jobs.sort(key=_created_sort_key, reverse=True)

    assert jobs[0] is naive_newest  # 2026-01-03 is newest
    assert jobs[1] is aware_new  # then 2026-01-02
    assert jobs[2] is aware_old  # then 2026-01-01
    assert jobs[-1] is missing  # None sorts oldest
