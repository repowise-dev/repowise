"""Git metadata and history writers."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger("repowise.core.pipeline.persist")


async def persist_git(
    result: Any, session: Any, repo_id: str, *, timings: Any | None = None
) -> None:
    """Persist git-phase outputs: per-file metadata and per-commit rows.

    Both writes are idempotent UPSERTs keyed by ``(repo_id, file_path)`` /
    ``(repo_id, sha)`` — safe to call incrementally and on resume.

    ``timings`` is an optional :class:`PhaseTimings` table; when given, the
    change-record rebuild (a first-parent history walk) is timed into it as
    ``persist.changes``.
    """
    from repowise.core.persistence.crud import (
        clear_attribution_if_disabled,
        prune_fix_events_before,
        update_repo_git_totals,
        upsert_fix_events_bulk,
        upsert_git_commit_files_bulk,
        upsert_git_commits_bulk,
        upsert_git_metadata_bulk,
    )
    from repowise.core.pipeline.phase_timing import timed

    if result.git_metadata_list:
        await upsert_git_metadata_bulk(session, repo_id, result.git_metadata_list)

    summary = getattr(result, "git_summary", None)

    # Per-commit rows + change-risk ride on the git summary.
    commit_rows = getattr(summary, "commit_rows", None)
    if commit_rows:
        await upsert_git_commits_bulk(session, repo_id, commit_rows)

    commit_file_rows = getattr(summary, "commit_file_rows", None)
    if commit_file_rows:
        await upsert_git_commit_files_bulk(session, repo_id, commit_file_rows)
    await clear_attribution_if_disabled(session, repo_id)
    if summary is not None:
        with timed(timings, "persist.changes"):
            await _rebuild_changes(session, repo_id)

    # Per fix-commit x file rows (with their SZZ candidates). The prune keeps a
    # re-index of an already-indexed repo from leaving behind events that have
    # since aged out of the defect window.
    fix_event_rows = getattr(summary, "fix_event_rows", None)
    if fix_event_rows:
        await upsert_fix_events_bulk(session, repo_id, fix_event_rows)
    oldest_ts = getattr(summary, "fix_oldest_ts", 0)
    if oldest_ts and getattr(summary, "fix_events_built", False):
        from datetime import UTC, datetime

        await prune_fix_events_before(session, repo_id, datetime.fromtimestamp(oldest_ts, tz=UTC))

    # Symbol attribution + bug-magnet rollups, over the rows just written.
    # Failure-isolated: a rollup that cannot be computed leaves the previous
    # values in place rather than blanking a surface mid-index.
    try:
        from repowise.core.pipeline.fix_rollups import apply_fix_rollups

        await apply_fix_rollups(session, repo_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("fix_rollups_failed", repo_id=repo_id, error=str(exc))

    # Whole-history totals (true age / commit / contributor counts) also ride on
    # the summary — stamp them on the Repository row so the stats page reads them
    # instead of deriving them from the bounded ``git_commits`` sample (#730).
    totals = getattr(summary, "repo_totals", None)
    if totals is not None:
        await update_repo_git_totals(
            session,
            repo_id,
            total_commit_count=totals.total_commit_count,
            total_merge_commit_count=getattr(totals, "total_merge_commit_count", None),
            first_commit_at=totals.first_commit_at,
            total_contributor_count=totals.total_contributor_count,
            first_commit_author=totals.first_commit_author,
            first_commit_subject=totals.first_commit_subject,
            total_lines_added=totals.total_lines_added,
            total_lines_deleted=totals.total_lines_deleted,
            churn_anchor_sha=getattr(totals, "churn_anchor_sha", None),
        )


async def _rebuild_changes(session: Any, repo_id: str) -> None:
    """Change records from the same history, after the commit labels they read."""
    from repowise.core.changes.store import refresh_changes
    from repowise.core.persistence.models import Repository

    try:
        repo = await session.get(Repository, repo_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("changes_repo_lookup_failed", repo_id=repo_id, error=str(exc))
        return
    if repo is not None and repo.local_path:
        await refresh_changes(session, repo_id, repo.local_path, rebuild=True)


async def replace_git_history(
    session: Any,
    repo_id: str,
    git_meta_map: dict[str, dict],
    git_summary: Any,
    *,
    keep_commit_health: bool = False,
) -> None:
    """Replace every history-derived row from one authoritative Git walk.

    ``keep_commit_health`` keeps the per-commit health rows. A walk that only
    re-reads the same window under new rules leaves each commit's code, so its
    health is still right, and the capped ``refresh_commit_health`` could not
    rebuild rows past its budget. Those rows key on ``(repository_id, sha)``
    alone, so nothing dangles; unreachable shas are pruned by the update's
    orphan sweep.
    """
    from types import SimpleNamespace

    from sqlalchemy import delete

    from repowise.core.persistence.models import (
        FixEvent,
        GitCommit,
        GitCommitFile,
        GitCommitHealthDelta,
        GitCommitHealthFinding,
        GitMetadata,
    )

    # Function blame describes the current source tree and is produced by the
    # health pass, not by persist_git. Preserve it across a history-window
    # replacement; scope reconciliation prunes entries for excluded files.
    health = () if keep_commit_health else (GitCommitHealthDelta, GitCommitHealthFinding)
    for model in (FixEvent, GitCommit, GitCommitFile, *health, GitMetadata):
        await session.execute(delete(model).where(model.repository_id == repo_id))
    await persist_git(
        SimpleNamespace(
            git_metadata_list=list(git_meta_map.values()),
            git_summary=git_summary,
        ),
        session,
        repo_id,
    )


async def persist_git_refresh(
    session: Any,
    repo_id: str,
    git_meta_map: dict[str, dict],
    git_decay_map: dict[str, dict] | None,
    full_git_summary: Any | None,
    *,
    keep_commit_health: bool = False,
) -> None:
    """Persist either a full history replacement or an incremental refresh."""
    if full_git_summary is not None:
        await replace_git_history(
            session,
            repo_id,
            git_meta_map,
            full_git_summary,
            keep_commit_health=keep_commit_health,
        )
        return

    from sqlalchemy import select

    from repowise.core.persistence.crud import (
        clear_attribution_if_disabled,
        recompute_git_percentiles,
        upsert_git_metadata_bulk,
    )
    from repowise.core.persistence.models import GitMetadata

    decay_rows = list((git_decay_map or {}).values())
    if decay_rows:
        stored = set(
            (
                await session.execute(
                    select(GitMetadata.file_path).where(GitMetadata.repository_id == repo_id)
                )
            ).scalars()
        )
        # A decay row is partial, so it only refreshes a stored row: inserted,
        # it is a fragment with no totals or owner. History-tier rows are
        # complete and upsert whole, which also fills the non-code rows an
        # index built before that tier never wrote.
        decay_rows = [
            row for row in decay_rows if row.get("history_only") or row["file_path"] in stored
        ]
    await upsert_git_metadata_bulk(session, repo_id, [*git_meta_map.values(), *decay_rows])
    await recompute_git_percentiles(session, repo_id)
    await clear_attribution_if_disabled(session, repo_id)
