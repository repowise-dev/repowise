"""Commit, fix-event and repo-total capture for an incremental run."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

from repowise.core.pipeline.phase_timing import PhaseTimings, timed

from ._common import logger


async def persist_incremental_commits(
    session: Any, repo_id: str, repo_path: Any, *, timings: PhaseTimings | None = None
) -> None:
    """Capture + upsert ``git_commits`` rows for commits new since the last index.

    Foundation 1 only populated the per-commit table on the full orchestrator
    index; without this, the commits/change-risk surface goes stale between full
    re-indexes. Bounds the walk to commits newer than the newest persisted
    ``committed_at`` (one ``git log`` pass) and upserts (idempotent on sha).
    """
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.persistence.crud import (
        clear_attribution_if_disabled,
        get_latest_commit_committed_at,
        get_repository,
        update_repo_git_totals,
        upsert_git_commit_files_bulk,
        upsert_git_commits_bulk,
    )
    from repowise.core.repo_config import load_repo_config

    cfg = load_repo_config(repo_path)
    indexer = GitIndexer(
        repo_path,
        commit_limit=cfg.get("commit_limit"),
        follow_renames=cfg.get("follow_renames", False),
        # The fix-event capture walks the tracked-file set itself, so without
        # the repo's excludes an update would store events for files a full
        # index never sees, and they would never age out.
        exclude_patterns=cfg.get("exclude_patterns"),
        # Git episodes ride the same capture and inherit those excludes.
        record_episodes=True,
    )
    newest = await get_latest_commit_committed_at(session, repo_id)
    since_ts: int | None = None
    if newest is not None:
        # SQLite drops tzinfo, so a naive read must be interpreted as UTC (the
        # column is stored tz-aware) rather than local time.
        from datetime import UTC

        dt = newest if newest.tzinfo is not None else newest.replace(tzinfo=UTC)
        since_ts = int(dt.timestamp())
    with timed(timings, "persist.commits.capture"):
        # One walk fills both, so they describe the same commit set.
        file_rows: list[dict] = []
        rows = await asyncio.to_thread(
            partial(
                indexer.capture_new_commit_rows,
                since_ts=since_ts,
                file_rows_sink=file_rows,
            )
        )
        if rows:
            await upsert_git_commits_bulk(session, repo_id, rows)
        if file_rows:
            await upsert_git_commit_files_bulk(session, repo_id, file_rows)
    await clear_attribution_if_disabled(session, repo_id)

    # What the new commits did to health is scanned by the caller once this
    # transaction commits (commit_health.refresh_commit_health_after_update):
    # the scan can take minutes and must not hold the write lock.

    with timed(timings, "persist.commits.experience"):
        await reconcile_commit_experience(session, repo_id, indexer)
    # Fills the commit-offset column on indexes written before it existed, so a
    # new capture never needs a re-index to become useful.
    with timed(timings, "persist.commits.offsets"):
        await reconcile_commit_offsets(session, repo_id, indexer)
    with timed(timings, "persist.commits.changes"):
        from repowise.core.changes.store import reconcile_categories, refresh_changes

        await refresh_changes(session, repo_id, repo_path, rebuild=False)
        try:
            await reconcile_categories(session, repo_id)
        except Exception as exc:
            logger.debug("category_reconcile_failed", error=str(exc))

    # Refresh the repo-level whole-history totals so age / commit / contributor
    # counts keep growing between full re-indexes (#730). Cheap git calls, and
    # cheap to run every update since they don't touch the bounded sample —
    # except lifetime churn, which walks the history. Handing the capture what
    # was stored last time lets it add only the range since, and it re-proves
    # that range is safe to add before doing so.
    prior = _churn_prior(await get_repository(session, repo_id))
    with timed(timings, "persist.commits.totals"):
        totals = await asyncio.to_thread(indexer.capture_repo_totals, prior)
    await update_repo_git_totals(
        session,
        repo_id,
        total_commit_count=totals.total_commit_count,
        total_merge_commit_count=totals.total_merge_commit_count,
        first_commit_at=totals.first_commit_at,
        total_contributor_count=totals.total_contributor_count,
        first_commit_author=totals.first_commit_author,
        first_commit_subject=totals.first_commit_subject,
        total_lines_added=totals.total_lines_added,
        total_lines_deleted=totals.total_lines_deleted,
        churn_anchor_sha=totals.churn_anchor_sha,
    )

    with timed(timings, "persist.commits.fix_events"):
        await persist_incremental_fix_events(session, repo_id, indexer)


def _churn_prior(repo_row: Any) -> Any:
    """The stored totals lifetime churn can resume from, or ``None``.

    Only the four fields the fold reads are carried across, so this never
    becomes a second way to read repo-level git facts. ``None`` for a repo row
    that is missing or has no anchor yet, which makes the capture walk the whole
    history exactly as it always did.
    """
    from repowise.core.ingestion.git_indexer.records import RepoTotals

    if repo_row is None:
        return None
    anchor = getattr(repo_row, "churn_anchor_sha", None)
    if not anchor:
        return None
    return RepoTotals(
        total_commit_count=repo_row.total_commit_count,
        total_lines_added=repo_row.total_lines_added,
        total_lines_deleted=repo_row.total_lines_deleted,
        churn_anchor_sha=anchor,
    )


async def reconcile_commit_experience(session: Any, repo_id: str, indexer: Any) -> None:
    """Re-tally author experience across the whole commit table and re-score.

    ``build_commit_rows`` can only count the commits it is handed. On a full
    index that is the entire window, so the number lands right; on an update it
    is just the commits newer than the last one persisted, so every author's
    count restarts at zero and an established author's new commits look like a
    first-timer's. That is not only a cosmetic badge: ``author_experience`` is a
    change-risk feature, so the stored ``change_risk_score`` inherits the error
    and the review queue ranks on it.

    Rather than seed the batch tally, this re-derives experience over the full
    persisted history after the append. The batch-local pass then has no lasting
    say, so there is no second code path to keep correct, and rows written by
    earlier (wrong) updates are repaired on the next one instead of waiting for
    a re-index.

    Two things have to happen together. Unreachable commits are dropped first:
    an update run on a feature branch persists that branch's shas, and once it
    is squash-merged they survive as orphans that no full index would produce
    and inflate every author's count. Then each surviving commit is re-scored
    from its stored features, because a corrected experience that leaves the old
    score in place would just move the inconsistency somewhere less visible.

    Bounded by the commit count and git-free apart from one ``rev-list``, and
    failure-isolated like the rest of the git-phase refreshes.
    """
    from repowise.core.persistence.crud import (
        delete_commit_health_by_sha,
        delete_git_commit_files_by_sha,
        delete_git_commits_by_sha,
        get_commit_experience_inputs,
        upsert_git_commits_bulk,
    )

    try:
        stored = await get_commit_experience_inputs(session, repo_id)
        if not stored:
            return

        reachable = await asyncio.to_thread(indexer.list_reachable_shas)
        if reachable is not None:
            orphans = [r["sha"] for r in stored if r["sha"] not in reachable]
            if orphans:
                await delete_git_commits_by_sha(session, repo_id, orphans)
                # Per-commit rows leave with their commit; nothing cascades them.
                await delete_git_commit_files_by_sha(session, repo_id, orphans)
                await delete_commit_health_by_sha(session, repo_id, orphans)
                stored = [r for r in stored if r["sha"] in reachable]
                logger.info("commit_orphans_pruned", repo_id=repo_id, count=len(orphans))

        updates = _recompute_commit_experience(stored)
        if updates:
            await upsert_git_commits_bulk(session, repo_id, updates)
            logger.info("commit_experience_reconciled", repo_id=repo_id, count=len(updates))
    except Exception as exc:
        logger.debug("commit_experience_reconcile_failed", error=str(exc))


async def reconcile_commit_offsets(session: Any, repo_id: str, indexer: Any) -> None:
    """Backfill ``committed_offset_minutes`` on commits indexed before it existed.

    The offset is captured on the commit walk, so a full re-index gets it for
    free — but an update only writes rows for *new* commits, which would leave a
    repo indexed earlier with author-local hours on recent commits and UTC on
    everything older. A punch card mixing the two is worse than one that is
    honestly all-UTC, so the gap is closed here rather than waiting for a
    re-index nobody should have to run for a new column.

    One ``git log`` and one bulk update, both skipped entirely once the column
    is filled — the common case is a single cheap SELECT that returns nothing.
    Failure-isolated like the other update-time reconciles; an unfilled offset
    just means those rows keep falling back to UTC.
    """
    from repowise.core.persistence.crud import (
        get_commits_missing_offset,
        upsert_git_commits_bulk,
    )

    try:
        pending = await get_commits_missing_offset(session, repo_id)
        if not pending:
            return

        offsets = await asyncio.to_thread(indexer.capture_commit_offsets, pending)
        if not offsets:
            return

        await upsert_git_commits_bulk(
            session,
            repo_id,
            [{"sha": sha, "committed_offset_minutes": minutes} for sha, minutes in offsets.items()],
        )
        logger.info("commit_offsets_backfilled", repo_id=repo_id, count=len(offsets))
    except Exception as exc:
        logger.debug("commit_offset_reconcile_failed", error=str(exc))


def _committed_ts(committed_at: Any) -> float:
    """``committed_at`` as a sortable epoch, or 0 when the row has no date.

    SQLite drops tzinfo, so a naive read has to be reinterpreted as UTC — the
    column is stored tz-aware. Doing that first also keeps ``timestamp()`` off
    the platform's local-time conversion, which fails outright on Windows for
    pre-epoch dates.
    """
    if committed_at is None:
        return 0.0
    from datetime import UTC

    if committed_at.tzinfo is None:
        committed_at = committed_at.replace(tzinfo=UTC)
    return committed_at.timestamp()


def _recompute_commit_experience(stored: list[dict]) -> list[dict]:
    """Rows whose experience or risk moved, as partial upserts keyed by sha.

    Pure, so the ordering and re-scoring can be tested without a database.
    Emits only changed rows: on a settled index that is a handful, which keeps a
    whole-table read from turning into a whole-table write every update.
    """
    from repowise.core.analysis.change_risk import change_features_from_stored, score_change
    from repowise.core.ingestion.git_indexer.commit_rows import author_experience_by_sha

    exp_by_sha = author_experience_by_sha(
        [
            {
                "sha": r["sha"],
                "author_name": r["author_name"] or "",
                "author_email": r["author_email"] or "",
                # committed_at is the only ordering key the table stores; rows
                # without one sort oldest, which is where an unknown date least
                # disturbs everyone else's running count.
                "ts": _committed_ts(r["committed_at"]),
            }
            for r in stored
        ]
    )

    updates: list[dict] = []
    for r in stored:
        exp = exp_by_sha.get(r["sha"], 0)
        risk = score_change(
            change_features_from_stored(
                la=r["lines_added"],
                ld=r["lines_deleted"],
                nf=r["files_changed"],
                nd=r["dirs_changed"],
                ns=r["subsystems_changed"],
                entropy=r["entropy"],
                exp=exp,
                is_fix=r["is_fix"],
                author=r["author_name"],
                subject=r["subject"],
                ref=r["sha"],
            )
        )
        unchanged = (
            r["author_experience"] == exp
            and r["change_risk_level"] == risk.level
            and r["change_risk_score"] is not None
            and abs(r["change_risk_score"] - risk.score) < 1e-9
        )
        if unchanged:
            continue
        updates.append(
            {
                "sha": r["sha"],
                "author_experience": exp,
                "change_risk_score": risk.score,
                "change_risk_level": risk.level,
            }
        )
    return updates


async def persist_incremental_fix_events(session: Any, repo_id: str, indexer: Any) -> None:
    """Trace + upsert ``fix_events`` for fix commits this index has not seen.

    Both halves are needed for an update to converge on what a fresh index would
    produce: append the fix commits missing from the table, then drop the ones that
    aged out of the window, lost their file, or the fix rule no longer names.
    Failure-isolated, like every other git-phase refresh.
    """
    from repowise.core.persistence.crud import (
        get_fix_event_shas,
        prune_fix_events_before,
        prune_fix_events_for_missing_paths,
        prune_fix_events_for_shas,
        upsert_fix_events_bulk,
    )

    try:
        known = await get_fix_event_shas(session, repo_id)
        rows, oldest_ts, tracked, retracted = await asyncio.to_thread(
            indexer.capture_new_fix_events, known_shas=known
        )
    except Exception as exc:
        logger.debug("incremental_fix_events_failed", error=str(exc))
        return

    if rows:
        await upsert_fix_events_bulk(session, repo_id, rows)
    # A zero cutoff means the walk or the trace failed; pruning then would drop
    # rows nothing replaced.
    if oldest_ts:
        from datetime import UTC, datetime

        await prune_fix_events_before(session, repo_id, datetime.fromtimestamp(oldest_ts, tz=UTC))
    if tracked:
        await prune_fix_events_for_missing_paths(session, repo_id, tracked)
    await prune_fix_events_for_shas(session, repo_id, retracted)

    # Recompute over the whole stored window, not just the rows this update
    # appended: decay ages every file's mass whether or not the file changed.
    try:
        from repowise.core.pipeline.fix_rollups import apply_fix_rollups

        await apply_fix_rollups(session, repo_id)
    except Exception as exc:
        logger.debug("fix_rollups_failed", error=str(exc))
