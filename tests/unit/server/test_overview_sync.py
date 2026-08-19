"""Tests for the ``sync`` block of /api/repos/{id}/overview-summary.

The "Last synced" timestamp must reflect CLI / git-hook auto-syncs
(``repowise update``), which refresh the index and bump
``repositories.updated_at`` but never create a GenerationJob row. A
job-only derivation reported "never synced" even when the index was current.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GenerationJob
from tests.unit.server.conftest import create_test_repo


async def _complete_job(app, repo_id: str, *, mode: str, finished_at: datetime) -> None:
    async with get_session(app.state.session_factory) as session:
        job = await crud.upsert_generation_job(
            session,
            repository_id=repo_id,
            status="completed",
            config={"mode": mode},
        )
        row = (
            await session.execute(select(GenerationJob).where(GenerationJob.id == job.id))
        ).scalar_one()
        row.status = "completed"
        row.finished_at = finished_at


@pytest.mark.asyncio
async def test_last_sync_falls_back_to_repo_updated_at(client: AsyncClient) -> None:
    """No completed sync job: last_sync_at uses repositories.updated_at."""
    repo = await create_test_repo(client)

    resp = await client.get(f"/api/repos/{repo['id']}/overview-summary")
    assert resp.status_code == 200
    body = resp.json()

    assert body["sync"]["last_sync_at"] is not None
    assert body["sync"]["last_sync_at"] == body["repo"]["updated_at"]


@pytest.mark.asyncio
async def test_fallback_supersedes_older_full_resync(client: AsyncClient, app) -> None:
    """An auto-sync after a full re-index shows as the latest sync, not the resync."""
    repo = await create_test_repo(client)
    await _complete_job(app, repo["id"], mode="full_resync", finished_at=datetime(2020, 1, 1))

    resp = await client.get(f"/api/repos/{repo['id']}/overview-summary")
    assert resp.status_code == 200
    body = resp.json()

    # repo.updated_at (now) is newer than the 2020 resync -> fallback wins.
    assert body["sync"]["last_resync_at"] is not None
    assert body["sync"]["last_sync_at"] == body["repo"]["updated_at"]
    assert body["sync"]["last_sync_at"] != body["sync"]["last_resync_at"]


@pytest.mark.asyncio
async def test_completed_sync_job_wins_over_fallback(client: AsyncClient, app) -> None:
    """A real completed sync job is preferred over the updated_at fallback."""
    repo = await create_test_repo(client)
    finished = datetime(2030, 1, 1)
    await _complete_job(app, repo["id"], mode="sync", finished_at=finished)

    resp = await client.get(f"/api/repos/{repo['id']}/overview-summary")
    assert resp.status_code == 200
    body = resp.json()

    assert body["sync"]["last_sync_at"] == finished.isoformat()
    assert body["sync"]["last_sync_at"] != body["repo"]["updated_at"]


@pytest.mark.asyncio
async def test_head_commit_heals_from_state_json(client: AsyncClient, app, tmp_path) -> None:
    """overview-summary serves state.json's last_sync_commit over a stale DB
    head_commit, matching /api/repos and /health/overview so the dashboard's
    indexed-commit display never reads "behind" after a no-op update left the
    repositories row un-restamped.
    """
    from repowise.core.persistence.models import Repository

    stale = "a" * 40
    current = "c" * 40

    repo = await create_test_repo(client, tmp_path)
    repowise = Path(repo["local_path"]) / ".repowise"
    repowise.mkdir(exist_ok=True)
    (repowise / "state.json").write_text(f'{{"last_sync_commit": "{current}"}}', encoding="utf-8")

    # DB row left at an older commit (the un-restamped case the heal repairs).
    async with get_session(app.state.session_factory) as session:
        row = (
            await session.execute(select(Repository).where(Repository.id == repo["id"]))
        ).scalar_one()
        row.head_commit = stale

    resp = await client.get(f"/api/repos/{repo['id']}/overview-summary")
    assert resp.status_code == 200
    assert resp.json()["repo"]["head_commit"] == current


@pytest.mark.asyncio
async def test_head_commit_falls_back_to_db_without_state_json(
    client: AsyncClient, app, tmp_path
) -> None:
    """No state.json (e.g. a hosted index): fall back to the DB head_commit."""
    from repowise.core.persistence.models import Repository

    db_commit = "d" * 40
    repo = await create_test_repo(client, tmp_path)
    async with get_session(app.state.session_factory) as session:
        row = (
            await session.execute(select(Repository).where(Repository.id == repo["id"]))
        ).scalar_one()
        row.head_commit = db_commit

    resp = await client.get(f"/api/repos/{repo['id']}/overview-summary")
    assert resp.status_code == 200
    assert resp.json()["repo"]["head_commit"] == db_commit


@pytest.mark.asyncio
async def test_sync_includes_index_storage_bytes(client: AsyncClient, tmp_path) -> None:
    """overview-summary reports total .repowise on-disk footprint."""
    repo = await create_test_repo(client, tmp_path)
    repo_dir = Path(repo["local_path"])
    repowise = repo_dir / ".repowise"
    repowise.mkdir()
    (repowise / "wiki.db").write_bytes(b"x" * 100)

    resp = await client.get(f"/api/repos/{repo['id']}/overview-summary")
    assert resp.status_code == 200
    assert resp.json()["sync"]["index_storage_bytes"] == 100
