"""When did an analysis last run for a repository.

``None`` means never ran or unknown, and is never replaced by ``now()``.

Two sources, the later wins. The stage stamp (``Repository.settings_json``
``analysis_ran_at``) is written on the success path of the stage's own write,
so a run that found nothing still counts and a failed stage does not. Findings
rows are older evidence that covers indexes written before stamps existed.
A completed index job is deliberately not used: it proves the pipeline
finished, not that this best-effort stage did.

Ceiling: a stamp records that the stage persisted, and an incremental update
stamps after scanning only the changed files.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.crud.repository import ANALYSIS_RAN_KEY
from repowise.core.persistence.models import DeadCodeFinding, Repository, SecurityFinding


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite hands back naive datetimes; every writer stores UTC.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


async def _stamp(session: AsyncSession, repo_id: str, stage: str) -> datetime | None:
    raw = await session.scalar(select(Repository.settings_json).where(Repository.id == repo_id))
    try:
        stamps = json.loads(raw or "{}").get(ANALYSIS_RAN_KEY)
        return _aware(datetime.fromisoformat(stamps[stage]))
    except (TypeError, ValueError, KeyError, AttributeError):
        return None


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
    return _latest(rows, await _stamp(session, repo_id, "dead_code"))


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
    return _latest(rows, await _stamp(session, repo_id, "security"))
