"""When did an analysis last run for a repository.

``None`` means never ran or unknown, and is never replaced by ``now()``.

Findings rows alone cannot answer this: a run that finds nothing writes no
rows, so an all-clear repo would read as "never ran". The newest completed
index job (init, update, upgrade and the web re-analyze all record one) covers
that case. The result is the later of the two sources.

Ceiling: a completed job proves the pipeline finished, not that this stage ran
cleanly (the security scan is best-effort inside it). Treat the value as
"last index that could have produced these results".
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import DeadCodeFinding, GenerationJob, SecurityFinding


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite hands back naive datetimes; every writer stores UTC.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


async def _last_completed_job(session: AsyncSession, repo_id: str) -> datetime | None:
    return _aware(
        await session.scalar(
            select(func.max(GenerationJob.finished_at)).where(
                GenerationJob.repository_id == repo_id,
                GenerationJob.status == "completed",
            )
        )
    )


def _latest(*times: datetime | None) -> datetime | None:
    known = [t for t in times if t is not None]
    return max(known) if known else None


async def dead_code_analyzed_at(session: AsyncSession, repo_id: str) -> datetime | None:
    rows = _aware(
        await session.scalar(
            select(func.max(DeadCodeFinding.analyzed_at)).where(
                DeadCodeFinding.repository_id == repo_id
            )
        )
    )
    return _latest(rows, await _last_completed_job(session, repo_id))


async def security_scanned_at(session: AsyncSession, repo_id: str) -> datetime | None:
    # Working-tree rows only: history rows come from a separate on-demand scan.
    rows = _aware(
        await session.scalar(
            select(func.max(SecurityFinding.detected_at)).where(
                SecurityFinding.repository_id == repo_id,
                func.coalesce(SecurityFinding.commit_sha, "") == "",
            )
        )
    )
    return _latest(rows, await _last_completed_job(session, repo_id))
