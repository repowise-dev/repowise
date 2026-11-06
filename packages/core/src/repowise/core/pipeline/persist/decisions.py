"""Decision store repairs, decision upserts and decision lifecycle passes.

Shared steps of every path that persists decision records.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable
from typing import Any

import structlog

logger = structlog.get_logger("repowise.core.pipeline.persist")


async def repair_decision_store(
    session: Any,
    repo_id: str,
    *,
    vector_store: Any = None,
    skip: Callable[[str, Exception], None] | None = None,
) -> None:
    """Run the idempotent store repairs, in order, each failure skipped.

    Runs whether or not the caller has new decisions: every repair fixes rows
    an earlier run left behind. A failure goes to ``skip(step, exc)`` when
    given, else to the debug log.
    """

    def _skipped(step: str, event: str, exc: Exception) -> None:
        if skip is not None:
            skip(step, exc)
        else:
            logger.debug(event, error=str(exc))

    # Restore records the semantic supersession detector retired before it was
    # turned off. ``superseded`` is a protected status, so nothing else will
    # ever walk these back, and a store that only stops retiring is still a
    # store with a quarter of its corpus hidden. Runs whether or not this run
    # produced decisions.
    #
    # BEFORE the purge below, not after: restoring puts a row back at
    # ``proposed``, which is exactly what the purge deletes. Un-retire first and
    # a run ends in one consistent state — good records visible, retired-source
    # records gone. The other order leaves a restored changelog row alive for
    # one run and silently deletes it on the next.
    try:
        from repowise.core.persistence.crud import unretire_auto_superseded

        await unretire_auto_superseded(session)
    except Exception as _unretire_err:
        _skipped("Decision unretire", "decision_unretire_skipped", _unretire_err)

    # One-shot drain of proposals left by retired extraction sources; without
    # this, DBs indexed before a removal keep a flooded review queue forever
    # (#751 for code_comment). Confirmed/dismissed rows are kept.
    try:
        from repowise.core.analysis.decision_provenance import RETIRED_SOURCES
        from repowise.core.persistence.crud import purge_proposed_decisions_by_source

        for _retired in RETIRED_SOURCES:
            await purge_proposed_decisions_by_source(session, repo_id, _retired)
    except Exception as _purge_err:
        _skipped("Retired-source decision purge", "decision_purge_skipped", _purge_err)

    # Re-stamp evidence rows left on a previous SOURCE_RANK ladder. Local stores
    # are created by ``init_db`` and never see Alembic, so the migration alone
    # would only reach hosted; this is the same repair on the path every store
    # takes. No-op scan once reconciled, and it runs whether or not this run
    # produced decisions, because a store with nothing new still holds the rows.
    try:
        from repowise.core.persistence.crud import reconcile_source_ranks

        await reconcile_source_ranks(session)
    except Exception as _rank_err:
        _skipped("Decision source-rank repair", "decision_rank_reconcile_skipped", _rank_err)

    # The same repair for the scoring formula, which leaves every input valid
    # and every stored score stale, so no filter can find it. Its own try, so
    # a failure here is not logged as the rank repair's.
    try:
        from repowise.core.persistence.crud import reconcile_decision_confidence

        await reconcile_decision_confidence(session)
    except Exception as _conf_err:
        _skipped("Decision confidence repair", "decision_confidence_reconcile_skipped", _conf_err)

    # Move legacy records onto ids derived from their own identity, before
    # anything else reads or writes one. A random id is re-minted whenever a
    # store is rebuilt rather than updated, which strands every reference held
    # outside the row. Idempotent, and it never deletes a record.
    try:
        from repowise.core.persistence.decision_id_migration import apply_id_migration

        await apply_id_migration(session, repo_id, vector_store=vector_store)
    except Exception as _id_err:
        _skipped("Decision id migration", "decision_id_migration_skipped", _id_err)

    # Classify legacy rows against the entity split. A record promoted by
    # recurrence rather than by a person becomes a candidate, which visibly
    # shrinks what governs; leaving it to an explicit command would instead
    # leave the status column and the acceptance log permanently disagreeing,
    # with half the surfaces reading each. Idempotent, and it never reopens a
    # review action somebody already performed.
    try:
        from repowise.core.persistence.decision_migration import (
            apply_migration,
            backfill_decision_node_links,
            backfill_scope_basis,
            backfill_session_scope_basis,
            prune_unindexed_scope_files,
        )

        await apply_migration(session, repo_id)
        # Beside it, and for the same reason: these repair rows written before
        # the rule existed, and nothing re-extracts them. The prune runs first
        # so the two basis repairs judge the file list they will leave behind.
        await prune_unindexed_scope_files(session, repo_id)
        await backfill_scope_basis(session, repo_id)
        await backfill_session_scope_basis(session, repo_id)
        await backfill_decision_node_links(session, repo_id)
    except Exception as _migrate_err:
        _skipped("Decision entity migration", "decision_entity_migration_skipped", _migrate_err)


async def store_decisions(
    session: Any,
    repo_id: str,
    decisions: list[dict],
    *,
    vector_store: Any = None,
    git_metadata: Iterable[Any] | None = None,
    provider: Any = None,
    repo_path: Any = None,
) -> list[str]:
    """Upsert ``decisions``, then detect supersession and rescore staleness.

    Returns the ids the upsert touched. A failing upsert propagates; the two
    passes after it are best-effort. ``git_metadata`` holds per-file git rows
    (dicts or dataclasses) for the staleness rescore; ``provider`` enables
    the LLM tiebreaker in supersession detection; ``repo_path`` lets the
    rescore start from when each record's evidence commits landed.
    """
    from repowise.core.persistence.crud import (
        bulk_upsert_decisions,
        recompute_decision_staleness,
    )

    if not decisions:
        return []
    # The run's shared vector store gives semantic (paraphrase) dedup and
    # makes decisions searchable; title dedup still runs when None.
    touched_ids = await bulk_upsert_decisions(
        session,
        repo_id,
        decisions,
        vector_store=vector_store,
    )
    # Detect supersession/conflict among the just-upserted decisions and
    # record typed edges (auto-flipping the older only above the
    # high-confidence threshold). Heuristic-only without a provider; with one,
    # the gated LLM tiebreaker runs too.
    if touched_ids and vector_store is not None:
        try:
            from repowise.core.analysis.decision_evolution import (
                detect_supersessions_and_conflicts,
            )

            evo = await detect_supersessions_and_conflicts(
                session,
                repo_id,
                touched_ids=touched_ids,
                vector_store=vector_store,
                provider=provider,
            )
            if any(evo.values()):
                logger.info("decision_supersession_detected", **evo)
        except Exception as _evo_err:
            logger.debug("supersession_detection_skipped", error=str(_evo_err))
    # Recompute staleness scores using git metadata.
    if git_metadata:
        try:
            git_meta_map: dict[str, dict] = {}
            for gm in git_metadata:
                gm_dict = gm if isinstance(gm, dict) else dataclasses.asdict(gm)
                fp = gm_dict.get("file_path", "")
                if fp:
                    git_meta_map[fp] = gm_dict
            if git_meta_map:
                updated = await recompute_decision_staleness(
                    session, repo_id, git_meta_map, repo_path
                )
                if updated:
                    logger.info("decision_staleness_recomputed", updated=updated)
        except Exception as _stale_err:
            logger.debug("staleness_scoring_skipped", error=str(_stale_err))
    return touched_ids


async def apply_decision_lifecycle(
    session: Any,
    repo_id: str,
    repo_path: Any,
    *,
    skip: Callable[[str, Exception], None] | None = None,
) -> None:
    """Retire decisions whose commits were reverted or whose HEAD artifacts are gone.

    Every run, whether or not it stored decisions. Each pass is best-effort: a
    failure goes to ``skip(step, exc)`` when given, else to the debug log.
    """
    # Retire records whose commits were reverted. Not tied to new records: a
    # new revert retires a decision stored by an earlier run just as well.
    try:
        from repowise.core.analysis.decisions.reverts import apply_revert_supersession

        await apply_revert_supersession(session, repo_id, repo_path)
    except Exception as _revert_err:
        if skip is not None:
            skip("Revert supersession", _revert_err)
        else:
            logger.debug("revert_supersession_skipped", error=str(_revert_err))
    try:
        from repowise.core.analysis.decisions.head_artifacts import apply_head_artifact_check

        await apply_head_artifact_check(session, repo_id, repo_path)
    except Exception as _artifact_err:
        if skip is not None:
            skip("Decision HEAD artifact check", _artifact_err)
        else:
            logger.debug("head_artifact_check_skipped", error=str(_artifact_err))
