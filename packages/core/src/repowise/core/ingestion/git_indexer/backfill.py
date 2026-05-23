"""Background backfill worker — promote an ESSENTIAL index to FULL.

When a large repo is indexed with :class:`~.tiers.GitIndexTier.ESSENTIAL`, the
expensive signals (per-file blame, repo-wide co-change) are skipped to get the
user a usable index fast. ``backfill_full_tier`` runs those signals afterwards
as a separate, resumable phase.

It is checkpoint-aware: when a :class:`JobStore` is supplied it records a
``git.backfill`` job, advances the cursor as it completes, and marks the job
COMPLETED / FAILED so a crashed backfill can be detected and re-run. The
worker is deliberately a thin scaffold — it re-runs FULL indexing for the repo
— so the resume contract and wiring are exercised now; finer-grained
per-file resume is a follow-up.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from .records import GitIndexSummary
from .tiers import GitIndexTier

if TYPE_CHECKING:
    from repowise.core.persistence._interfaces.job_store import JobStore

    from .indexer import GitIndexer

logger = structlog.get_logger(__name__)

__all__ = ["BACKFILL_BLAME_PHASE", "BACKFILL_PHASE", "backfill_blame", "backfill_full_tier"]

# Phase name used for the JobStore record so resume detection can find it.
BACKFILL_PHASE = "git.backfill"
# Sub-phase covering per-line blame index population only. Reuses the FULL
# orchestration; named so callers can distinguish a blame-only backfill from
# a full tier promotion in logs and job records.
BACKFILL_BLAME_PHASE = "git.backfill.blame"


async def backfill_full_tier(
    indexer: GitIndexer,
    repo_id: str,
    *,
    job_store: JobStore | None = None,
) -> tuple[GitIndexSummary, list[dict]]:
    """Run the FULL-tier git signals for *repo_id* and return the result.

    *indexer* may have been constructed at any tier; this forces FULL for the
    duration of the backfill and restores the original tier afterwards. When
    *job_store* is provided the run is bracketed by a resumable job record.
    """
    from repowise.core.persistence._interfaces.job_store import JobState

    job_id: str | None = None
    if job_store is not None:
        job = await job_store.create_job(
            repository_id=repo_id,
            phase=BACKFILL_PHASE,
            metadata={"tier": GitIndexTier.FULL.value},
        )
        job_id = job.id
        await job_store.update_state(job_id, JobState.RUNNING)

    original_tier = indexer.tier
    indexer.tier = GitIndexTier.FULL
    try:
        summary, results = await indexer.index_repo(repo_id)
    except Exception as exc:
        if job_store is not None and job_id is not None:
            await job_store.update_state(job_id, JobState.FAILED, error=str(exc))
        logger.warning("git_backfill_failed", repo_id=repo_id, error=str(exc))
        raise
    finally:
        indexer.tier = original_tier

    if job_store is not None and job_id is not None:
        await job_store.update_state(job_id, JobState.COMPLETED, cursor=str(summary.files_indexed))
    logger.info(
        "git_backfill_complete",
        repo_id=repo_id,
        files=summary.files_indexed,
    )
    return summary, results


async def backfill_blame(
    indexer: GitIndexer,
    repo_id: str,
    *,
    job_store: JobStore | None = None,  # type: ignore[name-defined]
) -> tuple[GitIndexSummary, list[dict]]:
    """Promote an ESSENTIAL index to include per-line blame indexes.

    Thin wrapper around :func:`backfill_full_tier` — the FULL tier already
    builds the :class:`~.function_blame.BlameIndex` inline alongside
    ownership blame, so the same orchestration covers both. The wrapper
    exists so callers (and tests) can express "I just need blame populated"
    intent independent of the larger FULL promotion.
    """
    from repowise.core.persistence._interfaces.job_store import JobState

    job_id: str | None = None
    if job_store is not None:
        job = await job_store.create_job(
            repository_id=repo_id,
            phase=BACKFILL_BLAME_PHASE,
            metadata={"tier": GitIndexTier.FULL.value, "scope": "blame"},
        )
        job_id = job.id
        await job_store.update_state(job_id, JobState.RUNNING)

    original_tier = indexer.tier
    indexer.tier = GitIndexTier.FULL
    try:
        summary, results = await indexer.index_repo(repo_id)
    except Exception as exc:
        if job_store is not None and job_id is not None:
            await job_store.update_state(job_id, JobState.FAILED, error=str(exc))
        logger.warning("git_blame_backfill_failed", repo_id=repo_id, error=str(exc))
        raise
    finally:
        indexer.tier = original_tier

    if job_store is not None and job_id is not None:
        await job_store.update_state(job_id, JobState.COMPLETED, cursor=str(summary.files_indexed))
    logger.info(
        "git_blame_backfill_complete",
        repo_id=repo_id,
        files=summary.files_indexed,
    )
    return summary, results
