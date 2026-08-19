"""Tests for APScheduler background jobs."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GenerationJob
from repowise.server.scheduler import setup_scheduler


async def test_polling_fallback_launches_persisted_job(session_factory, tmp_path) -> None:
    """A diverged repository should launch the sync job that was persisted."""
    repo_path = tmp_path / "repo"
    state_path = repo_path / ".repowise" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"last_sync_commit": "old-sha"}), encoding="utf-8")

    async with get_session(session_factory) as session:
        repo = await crud.upsert_repository(
            session,
            name="test-repo",
            local_path=str(repo_path),
        )
        repo_id = repo.id

    app_state = SimpleNamespace(background_tasks=set())
    scheduler = setup_scheduler(session_factory, app_state=app_state)
    polling_job = next(job for job in scheduler.get_jobs() if job.id == "polling_fallback")
    head_result = SimpleNamespace(returncode=0, stdout="new-sha\n")
    execute_job = AsyncMock()

    with (
        patch("subprocess.run", return_value=head_result),
        patch("repowise.server.job_executor.execute_job", execute_job),
    ):
        await polling_job.func()
        if app_state.background_tasks:
            await asyncio.gather(*app_state.background_tasks)

    execute_job.assert_awaited_once()
    job_id, launched_state = execute_job.await_args.args
    assert launched_state is app_state

    async with get_session(session_factory) as session:
        result = await session.execute(
            select(GenerationJob).where(GenerationJob.repository_id == repo_id)
        )
        persisted_job = result.scalar_one()

    assert job_id == persisted_job.id
    assert persisted_job.status == "pending"
    assert json.loads(persisted_job.config_json) == {
        "mode": "sync",
        "trigger": "polling_fallback",
        "before": "old-sha",
        "after": "new-sha",
    }
