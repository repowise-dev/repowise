"""Executes a :class:`RetentionPlan` against the store.

Split from :mod:`repowise.core.pipeline.retention` on the same line the rest of
the pipeline draws: the planner is pure and testable without a database, this
half knows about sessions and nothing about policy. The split also means a
caller can log or diff a plan before anything is deleted, which is the only way
a destructive sweep gets to be reviewed.

Deletes are chunked for the same reason every other sweep in this package is:
SQLite's bound-variable limit is a real ceiling on the local CLI store.
"""

from __future__ import annotations

from typing import Any

import structlog

from repowise.core.pipeline.retention import (
    RetentionPlan,
    RetentionPolicy,
    plan_version_retention,
)

logger = structlog.get_logger(__name__)

# Matches ``_STALE_ID_CHUNK`` / ``_PRUNE_CHUNK`` in ``persist.py``.
_DELETE_CHUNK = 500


async def load_version_rows(session: Any, repo_id: str, page_ids: list[str]) -> list[dict]:
    """The version history of *page_ids*, as the plain dicts the planner takes.

    Selects only the columns the policy reads. The ``content`` column is the
    reason this table is large, and pulling it here to decide what to delete
    would load the very thing the sweep exists to get rid of.
    """
    if not page_ids:
        return []
    from sqlalchemy import select

    from repowise.core.persistence.models import PageVersion

    rows: list[dict] = []
    for start in range(0, len(page_ids), _DELETE_CHUNK):
        chunk = page_ids[start : start + _DELETE_CHUNK]
        res = await session.execute(
            select(
                PageVersion.id,
                PageVersion.page_id,
                PageVersion.version,
                PageVersion.page_type,
                PageVersion.source_hash,
                PageVersion.model_name,
                PageVersion.provider_name,
                PageVersion.confidence,
                PageVersion.archived_at,
            ).where(PageVersion.repository_id == repo_id, PageVersion.page_id.in_(chunk))
        )
        rows.extend(dict(row._mapping) for row in res.all())
    return rows


async def apply_retention_plan(session: Any, repo_id: str, plan: RetentionPlan) -> int:
    """Delete the versions *plan* named. Returns the number of rows deleted."""
    if plan.is_empty:
        return 0
    from sqlalchemy import delete

    from repowise.core.persistence.models import PageVersion

    deleted = 0
    ids = plan.delete_ids
    for start in range(0, len(ids), _DELETE_CHUNK):
        chunk = ids[start : start + _DELETE_CHUNK]
        res = await session.execute(
            delete(PageVersion).where(
                PageVersion.repository_id == repo_id, PageVersion.id.in_(chunk)
            )
        )
        deleted += int(res.rowcount or 0)
    logger.info(
        "page_versions_pruned",
        repo_id=repo_id,
        deleted=deleted,
        examined=plan.examined,
        kept=plan.kept,
        pages=len(plan.pages_touched),
    )
    return deleted


async def prune_page_versions(
    session: Any,
    repo_id: str,
    page_ids: list[str],
    policy: RetentionPolicy | None = None,
) -> int:
    """Load, plan, and apply retention for *page_ids* in one step.

    Best-effort by construction: the caller is always in the middle of a
    persistence run that has already succeeded, and losing a version sweep is
    not worth losing an index over.
    """
    rows = await load_version_rows(session, repo_id, page_ids)
    if not rows:
        return 0
    plan = plan_version_retention(rows, policy)
    return await apply_retention_plan(session, repo_id, plan)


async def prune_repo_page_versions(
    session: Any,
    repo_id: str,
    policy: RetentionPolicy | None = None,
) -> int:
    """Sweep every page in the repo, not just a named set.

    A full index regenerates the whole wiki and archives a version of every
    page it replaced, which makes it both the moment the table grows fastest
    and the safe moment to prune: nothing downstream is mid-read.
    """
    from sqlalchemy import select

    from repowise.core.persistence.models import Page

    res = await session.execute(select(Page.id).where(Page.repository_id == repo_id))
    page_ids = [row[0] for row in res.all()]
    return await prune_page_versions(session, repo_id, page_ids, policy)
