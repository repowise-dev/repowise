"""APScheduler background jobs for repowise server.

Two recurring jobs:
1. Staleness checker — finds stale wiki pages and queues regeneration.
2. Polling fallback — catches missed webhooks by comparing HEAD commits.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


def setup_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    app_state: Any = None,
    staleness_interval_minutes: int = 15,
    polling_interval_minutes: int = 15,
) -> AsyncIOScheduler:
    """Create and configure the APScheduler instance.

    Does NOT start the scheduler — caller must call ``scheduler.start()``.

    Args:
        app_state: The FastAPI ``app.state`` object, needed to launch
            background pipeline jobs from the polling fallback.
    """
    scheduler = AsyncIOScheduler()

    async def check_staleness() -> None:
        """Find stale pages and log them for regeneration."""
        from sqlalchemy import select

        from repowise.core.persistence.database import get_session
        from repowise.core.persistence.models import Page, Repository

        try:
            async with get_session(session_factory) as session:
                result = await session.execute(select(Repository))
                repos = result.scalars().all()

                for repo in repos:
                    stale_result = await session.execute(
                        select(Page).where(
                            Page.repository_id == repo.id,
                            Page.freshness_status.in_(["stale", "expired"]),
                        )
                    )
                    stale = stale_result.scalars().all()
                    if stale:
                        logger.info(
                            "staleness_check",
                            extra={
                                "repo_id": repo.id,
                                "repo_name": repo.name,
                                "stale_count": len(stale),
                            },
                        )
        except Exception:
            logger.exception("staleness_check_failed")

    async def polling_fallback() -> None:
        """Check if repos have diverged from their last_sync_commit.

        Enqueues a sync job for any repo whose git HEAD differs from the
        stored ``last_sync_commit`` in state.json, unless a job is already
        pending or running.
        """
        import json as _json
        import subprocess
        from pathlib import Path as _Path

        from sqlalchemy import select

        from repowise.core.persistence import crud
        from repowise.core.persistence.database import get_session
        from repowise.core.persistence.models import GenerationJob, Repository

        try:
            async with get_session(session_factory) as session:
                result = await session.execute(select(Repository))
                repos = result.scalars().all()

                for repo in repos:
                    if not repo.local_path:
                        continue

                    # Read last_sync_commit from state.json
                    state_path = _Path(repo.local_path) / ".repowise" / "state.json"
                    stored_commit = None
                    if state_path.is_file():
                        try:
                            state_data = _json.loads(state_path.read_text(encoding="utf-8"))
                            stored_commit = state_data.get("last_sync_commit")
                        except Exception:
                            pass

                    # Get current git HEAD
                    try:
                        head_result = subprocess.run(
                            ["git", "rev-parse", "HEAD"],
                            cwd=repo.local_path,
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        if head_result.returncode != 0:
                            continue
                        current_head = head_result.stdout.strip()
                    except Exception:
                        continue

                    if stored_commit and current_head == stored_commit:
                        continue

                    # Check no active job already
                    active = await session.execute(
                        select(GenerationJob.id)
                        .where(GenerationJob.repository_id == repo.id)
                        .where(GenerationJob.status.in_(["pending", "running"]))
                        .limit(1)
                    )
                    if active.scalar_one_or_none() is not None:
                        continue

                    # Enqueue a sync job
                    await crud.upsert_generation_job(
                        session,
                        repository_id=repo.id,
                        status="pending",
                        config={
                            "mode": "sync",
                            "trigger": "polling_fallback",
                            "before": stored_commit or "",
                            "after": current_head,
                        },
                    )
                    await session.commit()

                    logger.info(
                        "polling_sync_enqueued",
                        extra={
                            "repo_id": repo.id,
                            "repo_name": repo.name,
                            "stored": stored_commit,
                            "head": current_head,
                        },
                    )

                    # Launch the job in the background if app_state is available
                    if app_state is not None:
                        from repowise.server.job_executor import execute_job

                        task = asyncio.create_task(
                            execute_job(job.id, app_state),
                            name=f"poll-job-{job.id}",
                        )
                        bg_tasks: set = getattr(app_state, "background_tasks", set())
                        bg_tasks.add(task)
                        task.add_done_callback(bg_tasks.discard)
        except Exception:
            logger.exception("polling_fallback_failed")

    scheduler.add_job(
        check_staleness,
        trigger=IntervalTrigger(minutes=staleness_interval_minutes),
        id="staleness_check",
        replace_existing=True,
    )

    scheduler.add_job(
        polling_fallback,
        trigger=IntervalTrigger(minutes=polling_interval_minutes),
        id="polling_fallback",
        replace_existing=True,
    )

    return scheduler
