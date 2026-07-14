"""Async-safety tests for APScheduler background jobs."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.server.scheduler import setup_scheduler


async def test_polling_fallback_does_not_block_event_loop(session_factory, tmp_path) -> None:
    """A slow git HEAD lookup should yield control to other async work."""
    repo_path = tmp_path / "repo"
    state_path = repo_path / ".repowise" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"last_sync_commit": "same-sha"}), encoding="utf-8")

    async with get_session(session_factory) as session:
        await crud.upsert_repository(
            session,
            name="test-repo",
            local_path=str(repo_path),
        )

    loop = asyncio.get_running_loop()
    event_loop_progressed = asyncio.Event()

    def slow_head_lookup(*args, **kwargs):
        loop.call_soon_threadsafe(event_loop_progressed.set)
        time.sleep(0.05)
        return SimpleNamespace(returncode=0, stdout="same-sha\n")

    scheduler = setup_scheduler(session_factory)
    polling_job = next(job for job in scheduler.get_jobs() if job.id == "polling_fallback")

    with patch("subprocess.run", side_effect=slow_head_lookup):
        await polling_job.func()

    assert event_loop_progressed.is_set()
