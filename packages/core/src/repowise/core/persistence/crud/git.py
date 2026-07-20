"""CRUD operations for the git domain (repowise persistence layer).

Split out of the former monolithic ``crud.py``; ``crud/__init__.py`` re-exports
every public name, so existing imports are unaffected.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    FixEvent,
    GitCommit,
    GitFunctionBlame,
    GitMetadata,
    _new_uuid,
    _now_utc,
)
from ._shared import _BATCH_SIZE, _batch_upsert_keyed

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


# Repo-wide-walk signals (co-change pairs, Hassan entropy). A pass that did
# not run the walk — ESSENTIAL tier, or an incremental update without the
# tracked-file set — reports the empty default for these; overwriting blindly
# wiped the init-computed values for exactly the files that change most.
_WALK_FIELD_EMPTIES: dict[str, tuple] = {
    "co_change_partners_json": ("[]", "", None),
    "change_entropy": (0, 0.0, None),
    # AI line share comes from the whole trace file, merged only into files
    # reindexed this pass. Preserve a prior non-empty share when a transient
    # trace-read failure (or a pass that couldn't read traces) would otherwise
    # write the zero default — same defensive contract as the walk fields.
    "agent_line_count": (0, None),
    "agent_line_model_json": ("{}", "", None),
}


def _update_git_metadata(existing: GitMetadata, meta: dict) -> None:
    for key, val in meta.items():
        if key in ("id", "repository_id") or not hasattr(existing, key):
            continue
        empties = _WALK_FIELD_EMPTIES.get(key)
        if empties is not None and val in empties:
            current = getattr(existing, key, None)
            if current is not None and current not in empties:
                continue  # keep the previously computed signal
        setattr(existing, key, val)
    existing.updated_at = _now_utc()


async def upsert_git_metadata_bulk(
    session: AsyncSession,
    repository_id: str,
    metadata_list: list[dict],
) -> None:
    """Bulk upsert git metadata rows in batches."""
    await _batch_upsert_keyed(
        session,
        GitMetadata,
        metadata_list,
        prefilter=(GitMetadata.repository_id == repository_id,),
        item_key_fn=lambda meta: meta.get("file_path", ""),
        row_key_fn=lambda row: row.file_path,
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

    Hotspot classification mirrors ``enrich.meets_hotspot_floors`` (issue #361):
    the repo-relative top-quartile gate AND the absolute activity floors —
    keep the two paths in sync.
    """
    from repowise.core.ingestion.git_indexer._constants import (
        HOTSPOT_HIGH_COMMITS_90D,
        HOTSPOT_MIN_COMMITS_90D,
        HOTSPOT_MIN_TEMPORAL_SCORE,
    )

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
                  AND git_metadata.commit_count_90d >= :min_commits_90d
                  AND (git_metadata.commit_count_90d >= :high_commits_90d
                       OR COALESCE(git_metadata.temporal_hotspot_score, 0.0)
                          >= :min_temporal_score)),
    change_entropy_pct = (SELECT erank FROM ranked WHERE ranked.id = git_metadata.id)
WHERE repository_id = :repo_id;
"""
    await session.execute(
        text(sql),
        {
            "repo_id": repository_id,
            "min_commits_90d": HOTSPOT_MIN_COMMITS_90D,
            "high_commits_90d": HOTSPOT_HIGH_COMMITS_90D,
            "min_temporal_score": HOTSPOT_MIN_TEMPORAL_SCORE,
        },
    )
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
    await _batch_upsert_keyed(
        session,
        GitCommit,
        commit_rows,
        prefilter=(GitCommit.repository_id == repository_id,),
        item_key_fn=lambda row: row.get("sha", ""),
        row_key_fn=lambda row: row.sha,
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


async def delete_git_commits_by_sha(
    session: AsyncSession, repository_id: str, shas: Sequence[str]
) -> int:
    """Drop specific per-commit rows. Returns how many were removed."""
    removed = 0
    for start in range(0, len(shas), _BATCH_SIZE):
        chunk = shas[start : start + _BATCH_SIZE]
        if not chunk:
            continue
        result = await session.execute(
            delete(GitCommit).where(
                GitCommit.repository_id == repository_id, GitCommit.sha.in_(chunk)
            )
        )
        removed += int(result.rowcount or 0)
    await session.flush()
    return removed


async def get_commit_experience_inputs(session: AsyncSession, repository_id: str) -> list[dict]:
    """Every persisted commit's identity, timestamp and stored risk features.

    The input to the update-time reconcile: enough to re-tally author experience
    across the whole history and re-score each commit without touching git or
    the diffs. Deliberately column-scoped rather than whole ORM rows, since this
    loads the full table on every update.
    """
    stmt = select(
        GitCommit.sha,
        GitCommit.author_name,
        GitCommit.author_email,
        GitCommit.committed_at,
        GitCommit.lines_added,
        GitCommit.lines_deleted,
        GitCommit.files_changed,
        GitCommit.dirs_changed,
        GitCommit.subsystems_changed,
        GitCommit.entropy,
        GitCommit.is_fix,
        GitCommit.subject,
        GitCommit.author_experience,
        GitCommit.change_risk_score,
        GitCommit.change_risk_level,
    ).where(GitCommit.repository_id == repository_id)
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings()]


async def get_author_commit_counts(session: AsyncSession, repository_id: str) -> list[tuple]:
    """``(author_name, author_email, count)`` per raw identity in the index.

    Raw because identities are folded by the caller — the canonicalization that
    decides whether two emails are one person lives in the git-indexer, not in
    SQL, and splitting it across both would let the two drift.
    """
    stmt = (
        select(
            GitCommit.author_name,
            GitCommit.author_email,
            func.count(),
        )
        .where(GitCommit.repository_id == repository_id)
        .group_by(GitCommit.author_name, GitCommit.author_email)
    )
    result = await session.execute(stmt)
    return [tuple(row) for row in result.all()]


def _commit_authorship_clause(authorship: str | None):
    """Optional authorship predicate: ``agent`` keeps agent-attributed commits,
    ``human`` keeps the rest. ``None`` / ``"all"`` means no filtering."""
    if authorship == "agent":
        return GitCommit.agent_name.is_not(None)
    if authorship == "human":
        return GitCommit.agent_name.is_(None)
    return None


async def count_git_commits(
    session: AsyncSession, repository_id: str, *, authorship: str | None = None
) -> int:
    """Count persisted commits for a repository."""
    stmt = (
        select(func.count()).select_from(GitCommit).where(GitCommit.repository_id == repository_id)
    )
    clause = _commit_authorship_clause(authorship)
    if clause is not None:
        stmt = stmt.where(clause)
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


async def get_latest_commit_committed_at(session: AsyncSession, repository_id: str):
    """Return the newest persisted ``committed_at`` for a repo, or None.

    Bounds the incremental commit-row walk: only commits newer than this need
    capturing on a ``repowise update``.
    """
    result = await session.execute(
        select(func.max(GitCommit.committed_at)).where(GitCommit.repository_id == repository_id)
    )
    return result.scalar_one_or_none()


async def get_git_commit(session: AsyncSession, repository_id: str, sha: str) -> GitCommit | None:
    """Return one commit by sha (or a unique prefix), or None."""
    result = await session.execute(
        select(GitCommit).where(
            GitCommit.repository_id == repository_id,
            GitCommit.sha.like(f"{sha}%"),
        )
    )
    return result.scalars().first()


async def get_commit_risk_scores(session: AsyncSession, repository_id: str) -> list[float]:
    """Return every persisted ``change_risk_score`` for a repository (unsorted).

    Used to build a repo-relative :class:`~repowise.core.analysis.change_risk.
    RiskNormalizer` so the commits surface can rank a commit against its own
    repo's distribution rather than the absolute calibration band. Bounded by
    the indexer's ``commit_limit``, so the full pull is cheap.
    """
    result = await session.execute(
        select(GitCommit.change_risk_score).where(
            GitCommit.repository_id == repository_id,
            GitCommit.change_risk_score.is_not(None),
        )
    )
    return [float(s) for s in result.scalars().all() if s is not None]


async def get_git_commits(
    session: AsyncSession,
    repository_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
    sort: str = "risk",
    authorship: str | None = None,
) -> list[GitCommit]:
    """Return a page of commits, sorted by change-risk (default) or recency.

    ``sort="risk"`` ranks by ``change_risk_score`` descending (the review-
    priority order); ``sort="date"`` ranks by ``committed_at`` descending.
    ``authorship`` optionally narrows to ``agent`` / ``human`` commits.
    """
    order = GitCommit.committed_at.desc() if sort == "date" else GitCommit.change_risk_score.desc()
    stmt = select(GitCommit).where(GitCommit.repository_id == repository_id)
    clause = _commit_authorship_clause(authorship)
    if clause is not None:
        stmt = stmt.where(clause)
    result = await session.execute(stmt.order_by(order).limit(limit).offset(offset))
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# GitFunctionBlame CRUD (per-function blame rollup)
# ---------------------------------------------------------------------------


def _update_git_function_blame(existing: GitFunctionBlame, row: dict) -> None:
    for key, val in row.items():
        # ``symbol_id`` is the natural key — never reassign it on update.
        if key not in ("id", "repository_id", "symbol_id") and hasattr(existing, key):
            setattr(existing, key, val)
    existing.updated_at = _now_utc()


async def upsert_git_function_blame_bulk(
    session: AsyncSession,
    repository_id: str,
    rows: list[dict],
) -> None:
    """Bulk upsert per-function blame rows (keyed ``repository_id`` + ``symbol_id``)."""
    await _batch_upsert_keyed(
        session,
        GitFunctionBlame,
        rows,
        prefilter=(GitFunctionBlame.repository_id == repository_id,),
        item_key_fn=lambda row: row.get("symbol_id", ""),
        row_key_fn=lambda row: row.symbol_id,
        update_fn=_update_git_function_blame,
        insert_fn=lambda row: GitFunctionBlame(
            id=_new_uuid(),
            repository_id=repository_id,
            **{
                k: v
                for k, v in row.items()
                if k not in ("id", "repository_id") and hasattr(GitFunctionBlame, k)
            },
        ),
        batch_size=_BATCH_SIZE,
    )


async def delete_git_function_blame(session: AsyncSession, repository_id: str) -> None:
    """Remove all per-function blame rows for a repository (clean reindex)."""
    await session.execute(
        delete(GitFunctionBlame).where(GitFunctionBlame.repository_id == repository_id)
    )
    await session.flush()


async def count_git_function_blame(session: AsyncSession, repository_id: str) -> int:
    """Count persisted per-function blame rows for a repository."""
    result = await session.execute(
        select(func.count())
        .select_from(GitFunctionBlame)
        .where(GitFunctionBlame.repository_id == repository_id)
    )
    return int(result.scalar_one() or 0)


async def get_git_function_blame(
    session: AsyncSession, repository_id: str, symbol_id: str
) -> GitFunctionBlame | None:
    """Return one per-function blame row by exact ``symbol_id``, or None."""
    result = await session.execute(
        select(GitFunctionBlame).where(
            GitFunctionBlame.repository_id == repository_id,
            GitFunctionBlame.symbol_id == symbol_id,
        )
    )
    return result.scalar_one_or_none()


async def get_git_function_blames(
    session: AsyncSession,
    repository_id: str,
    *,
    file_path: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[GitFunctionBlame]:
    """Return a page of per-function blame rows, hottest (most-modified) first.

    Optionally scoped to a single ``file_path`` (the per-file drill-down).
    """
    q = select(GitFunctionBlame).where(GitFunctionBlame.repository_id == repository_id)
    if file_path is not None:
        q = q.where(GitFunctionBlame.file_path == file_path)
    q = q.order_by(GitFunctionBlame.mod_count.desc()).limit(limit).offset(offset)
    result = await session.execute(q)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# FixEvent CRUD (per fix-commit x file, with SZZ candidates)
# ---------------------------------------------------------------------------


def _update_fix_event(existing: FixEvent, row: dict) -> None:
    for key, val in row.items():
        # ``fix_sha`` + ``file_path`` are the natural key — never reassigned.
        if key not in ("id", "repository_id", "fix_sha", "file_path") and hasattr(existing, key):
            setattr(existing, key, val)
    existing.updated_at = _now_utc()


async def upsert_fix_events_bulk(
    session: AsyncSession,
    repository_id: str,
    rows: list[dict],
) -> None:
    """Bulk upsert fix events (keyed ``repository_id`` + ``fix_sha`` + ``file_path``).

    Idempotent, so re-running an index or replaying the same update twice
    converges on the same table rather than duplicating rows.
    """
    await _batch_upsert_keyed(
        session,
        FixEvent,
        rows,
        prefilter=(FixEvent.repository_id == repository_id,),
        item_key_fn=lambda row: (row.get("fix_sha", ""), row.get("file_path", "")),
        row_key_fn=lambda row: (row.fix_sha, row.file_path),
        update_fn=_update_fix_event,
        insert_fn=lambda row: FixEvent(
            id=_new_uuid(),
            repository_id=repository_id,
            **{
                k: v
                for k, v in row.items()
                if k not in ("id", "repository_id") and hasattr(FixEvent, k)
            },
        ),
        batch_size=_BATCH_SIZE,
    )


async def prune_fix_events_before(
    session: AsyncSession, repository_id: str, cutoff: datetime
) -> int:
    """Drop fix events that have aged out of the trailing defect window.

    The full index seeds exactly the fix commits inside the window; updates
    append newer ones. Without this the persisted set would keep growing past
    the window's trailing edge and diverge from what a fresh index produces —
    which is precisely what ``validate_p2_incremental.py`` asserts against.
    Rows are pruned by their own ``committed_at``, never decayed in place.
    """
    result = await session.execute(
        delete(FixEvent).where(
            FixEvent.repository_id == repository_id,
            # A NULL timestamp (an unreadable ``%ct``) would otherwise make the
            # row immortal and invisible to every window.
            or_(FixEvent.committed_at < cutoff, FixEvent.committed_at.is_(None)),
        )
    )
    await session.flush()
    return int(result.rowcount or 0)


async def prune_fix_events_for_missing_paths(
    session: AsyncSession, repository_id: str, tracked_paths: set[str]
) -> int:
    """Drop fix events for files that are no longer tracked.

    A full index only ever sees files that exist at HEAD, so it never produces
    these rows; an update, which appends and never revisits, keeps them forever
    once a file is deleted. Without this the two paths diverge by exactly the
    repo's deletions, which is what ``validate_p2_incremental.py`` caught.

    Diffs the stored paths in Python rather than sending the whole tracked set
    into a ``NOT IN``: the stored set is hundreds of paths, the tracked set is
    thousands.
    """
    result = await session.execute(
        select(FixEvent.file_path).where(FixEvent.repository_id == repository_id).distinct()
    )
    stale = [path for path in result.scalars().all() if path not in tracked_paths]
    if not stale:
        return 0
    deleted = await session.execute(
        delete(FixEvent).where(
            FixEvent.repository_id == repository_id,
            FixEvent.file_path.in_(stale),
        )
    )
    await session.flush()
    return int(deleted.rowcount or 0)


async def get_fix_event_shas(session: AsyncSession, repository_id: str) -> set[str]:
    """Every fix sha already persisted for a repo.

    Bounds the incremental capture: an update traces only the fix commits not in
    this set. A sha set rather than a "newest committed_at" cutoff because a
    merge can land fix commits older than rows already stored, and a timestamp
    bound would skip those forever.
    """
    result = await session.execute(
        select(FixEvent.fix_sha).where(FixEvent.repository_id == repository_id).distinct()
    )
    return {sha for sha in result.scalars().all() if sha}
