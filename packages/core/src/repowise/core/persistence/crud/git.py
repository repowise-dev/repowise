"""CRUD operations for the git domain (repowise persistence layer).

Split out of the former monolithic ``crud.py``; ``crud/__init__.py`` re-exports
every public name, so existing imports are unaffected.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    GitCommit,
    GitMetadata,
    _new_uuid,
    _now_utc,
)
from ._shared import _BATCH_SIZE, _batch_upsert

# ---------------------------------------------------------------------------
# GitMetadata CRUD
# ---------------------------------------------------------------------------


async def upsert_git_metadata(
    session: AsyncSession,
    *,
    repository_id: str,
    file_path: str,
    **kwargs: object,
) -> GitMetadata:
    """Create or update a single GitMetadata row."""
    result = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repository_id,
            GitMetadata.file_path == file_path,
        )
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        for key, val in kwargs.items():
            if hasattr(existing, key):
                setattr(existing, key, val)
        existing.updated_at = _now_utc()
    else:
        existing = GitMetadata(
            id=_new_uuid(),
            repository_id=repository_id,
            file_path=file_path,
            **{k: v for k, v in kwargs.items() if hasattr(GitMetadata, k)},
        )
        session.add(existing)

    await session.flush()
    return existing


async def get_git_metadata(
    session: AsyncSession, repository_id: str, file_path: str
) -> GitMetadata | None:
    """Return GitMetadata for a specific file, or None."""
    result = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repository_id,
            GitMetadata.file_path == file_path,
        )
    )
    return result.scalar_one_or_none()


async def get_git_metadata_bulk(
    session: AsyncSession, repository_id: str, file_paths: list[str]
) -> dict[str, GitMetadata]:
    """Return a dict of file_path → GitMetadata for the given paths."""
    if not file_paths:
        return {}
    result = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repository_id,
            GitMetadata.file_path.in_(file_paths),
        )
    )
    return {gm.file_path: gm for gm in result.scalars().all()}


async def get_all_git_metadata(session: AsyncSession, repository_id: str) -> dict[str, GitMetadata]:
    """Return all GitMetadata rows for a repository."""
    result = await session.execute(
        select(GitMetadata).where(GitMetadata.repository_id == repository_id)
    )
    return {gm.file_path: gm for gm in result.scalars().all()}


def _update_git_metadata(existing: GitMetadata, meta: dict) -> None:
    for key, val in meta.items():
        if key not in ("id", "repository_id") and hasattr(existing, key):
            setattr(existing, key, val)
    existing.updated_at = _now_utc()


async def upsert_git_metadata_bulk(
    session: AsyncSession,
    repository_id: str,
    metadata_list: list[dict],
) -> None:
    """Bulk upsert git metadata rows in batches."""
    await _batch_upsert(
        session,
        GitMetadata,
        metadata_list,
        key_fn=lambda meta: (
            GitMetadata.repository_id == repository_id,
            GitMetadata.file_path == meta.get("file_path", ""),
        ),
        update_fn=_update_git_metadata,
        insert_fn=lambda meta: GitMetadata(
            id=_new_uuid(),
            repository_id=repository_id,
            **{
                k: v
                for k, v in meta.items()
                if k not in ("id", "repository_id") and hasattr(GitMetadata, k)
            },
        ),
        batch_size=_BATCH_SIZE,
    )


async def recompute_git_percentiles(
    session: AsyncSession,
    repository_id: str,
) -> int:
    """Recompute churn_percentile, is_hotspot, and change_entropy_pct using SQL
    PERCENT_RANK window functions.

    Called after incremental updates so that percentile rankings stay fresh
    without a full ``repowise init``.  Returns the number of rows updated.

    Primary churn ranking signal is temporal_hotspot_score (exponentially decayed
    churn); commit_count_90d is the tiebreak. change_entropy_pct ranks files by
    change_entropy ascending — zero-entropy files tie at the minimum (0.0), so
    they stay below the biomarker's ≥0.80 gate. Works on both SQLite (3.25+) and
    PostgreSQL.
    """
    # First check how many rows exist so we can return the count without an
    # extra query after the UPDATE.
    count_result = await session.execute(
        select(GitMetadata).where(GitMetadata.repository_id == repository_id)
    )
    rows = count_result.scalars().all()
    if not rows:
        return 0

    sql = """
WITH ranked AS (
  SELECT id,
    PERCENT_RANK() OVER (
      PARTITION BY repository_id
      ORDER BY COALESCE(temporal_hotspot_score, 0.0), commit_count_90d
    ) AS prank,
    PERCENT_RANK() OVER (
      PARTITION BY repository_id
      ORDER BY COALESCE(change_entropy, 0.0)
    ) AS erank
  FROM git_metadata
  WHERE repository_id = :repo_id
)
UPDATE git_metadata
SET churn_percentile = (SELECT prank FROM ranked WHERE ranked.id = git_metadata.id),
    is_hotspot = ((SELECT prank FROM ranked WHERE ranked.id = git_metadata.id) >= 0.75
                  AND git_metadata.commit_count_90d > 0),
    change_entropy_pct = (SELECT erank FROM ranked WHERE ranked.id = git_metadata.id)
WHERE repository_id = :repo_id;
"""
    await session.execute(text(sql), {"repo_id": repository_id})
    await session.flush()
    return len(rows)


# ---------------------------------------------------------------------------
# GitCommit CRUD (per-commit rows + just-in-time change-risk)
# ---------------------------------------------------------------------------


def _update_git_commit(existing: GitCommit, row: dict) -> None:
    for key, val in row.items():
        # ``sha`` is the natural key — never reassign it on update.
        if key not in ("id", "repository_id", "sha") and hasattr(existing, key):
            setattr(existing, key, val)
    existing.updated_at = _now_utc()


async def upsert_git_commits_bulk(
    session: AsyncSession,
    repository_id: str,
    commit_rows: list[dict],
) -> None:
    """Bulk upsert per-commit rows (keyed on ``repository_id`` + ``sha``)."""
    await _batch_upsert(
        session,
        GitCommit,
        commit_rows,
        key_fn=lambda row: (
            GitCommit.repository_id == repository_id,
            GitCommit.sha == row.get("sha", ""),
        ),
        update_fn=_update_git_commit,
        insert_fn=lambda row: GitCommit(
            id=_new_uuid(),
            repository_id=repository_id,
            **{
                k: v
                for k, v in row.items()
                if k not in ("id", "repository_id") and hasattr(GitCommit, k)
            },
        ),
        batch_size=_BATCH_SIZE,
    )


async def delete_git_commits(session: AsyncSession, repository_id: str) -> None:
    """Remove all per-commit rows for a repository (used before a clean reindex)."""
    await session.execute(delete(GitCommit).where(GitCommit.repository_id == repository_id))
    await session.flush()


async def count_git_commits(session: AsyncSession, repository_id: str) -> int:
    """Count persisted commits for a repository."""
    result = await session.execute(
        select(func.count()).select_from(GitCommit).where(GitCommit.repository_id == repository_id)
    )
    return int(result.scalar_one() or 0)


async def get_git_commit(session: AsyncSession, repository_id: str, sha: str) -> GitCommit | None:
    """Return one commit by sha (or a unique prefix), or None."""
    result = await session.execute(
        select(GitCommit).where(
            GitCommit.repository_id == repository_id,
            GitCommit.sha.like(f"{sha}%"),
        )
    )
    return result.scalars().first()


async def get_git_commits(
    session: AsyncSession,
    repository_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
    sort: str = "risk",
) -> list[GitCommit]:
    """Return a page of commits, sorted by change-risk (default) or recency.

    ``sort="risk"`` ranks by ``change_risk_score`` descending (the review-
    priority order); ``sort="date"`` ranks by ``committed_at`` descending.
    """
    order = GitCommit.committed_at.desc() if sort == "date" else GitCommit.change_risk_score.desc()
    result = await session.execute(
        select(GitCommit)
        .where(GitCommit.repository_id == repository_id)
        .order_by(order)
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())
