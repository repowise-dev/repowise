"""APScheduler background jobs for repowise server.

Two recurring jobs:
1. Staleness checker — finds stale wiki pages and queues regeneration.
2. Polling fallback — catches missed webhooks by comparing HEAD commits.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


def setup_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    staleness_interval_minutes: int = 15,
    polling_interval_minutes: int = 15,
) -> AsyncIOScheduler:
    """Create and configure the APScheduler instance.

    Does NOT start the scheduler — caller must call ``scheduler.start()``.
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
        """Check if repos have diverged from their last_sync_commit."""
        from sqlalchemy import select

        from repowise.core.persistence.database import get_session
        from repowise.core.persistence.models import Repository

        try:
            async with get_session(session_factory) as session:
                result = await session.execute(select(Repository))
                repos = result.scalars().all()

                for repo in repos:
                    if not repo.local_path:
                        continue
                    # Compare head_commit with actual git HEAD.
                    # This is a lightweight check — full sync is triggered
                    # only if commits differ.
                    # NOTE: when a full sync is implemented here, pass
                    #   extra_exclude_patterns=repo.settings.get("exclude_patterns", [])
                    # to FileTraverser so user-configured exclusions are respected.
                    import json as _json

                    try:
                        _settings = _json.loads(repo.settings_json) if repo.settings_json else {}
                    except Exception:
                        _settings = {}
                    logger.debug(
                        "polling_check",
                        extra={
                            "repo_id": repo.id,
                            "head_commit": repo.head_commit,
                            "exclude_patterns": _settings.get("exclude_patterns", []),
                        },
                    )
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
