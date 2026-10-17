"""Analysis-phase persistence: dead code, drift, health and decisions."""

from __future__ import annotations

import dataclasses
from typing import Any

import structlog

# Patchable steps are looked up on the package at call time, so a patch on
# ``repowise.core.pipeline.persist`` reaches this module too.
from repowise.core.pipeline import persist as _persist

logger = structlog.get_logger("repowise.core.pipeline.persist")


async def persist_analysis(result: Any, session: Any, repo_id: str) -> None:
    """Persist analysis-phase outputs: dead code, health, decisions, drift.

    Dead-code, health and doc-drift writes are repo-wide DELETE-THEN-INSERT (so
    they converge on re-run but don't support partial-within-phase resume);
    decisions/governance are idempotent. Intended to run once the analysis
    phase has fully completed.
    """
    from repowise.core.persistence.crud import (
        bulk_upsert_decisions,
        recompute_decision_staleness,
        replace_doc_drift_guarded,
        save_coverage_files,
        save_dead_code_findings,
        set_repo_function_mod_p80,
        upsert_git_function_blame_bulk,
    )

    # ---- Dead code findings --------------------------------------------------
    # Written even when empty: a run that found nothing must clear the old
    # open rows, not leave them standing as current findings.
    if result.dead_code_report is not None:
        await save_dead_code_findings(session, repo_id, result.dead_code_report.findings or [])

    # ---- Documentation drift: findings and resolved references --------------
    # Both tables, one savepoint, one run. Written even when the finding list
    # is empty, unlike dead code above: a run that fixed the last drifted
    # reference must clear the rows, and a guard on ``.findings`` would leave
    # the old ones standing as though the documents were still wrong.
    # ``authoritative_paths`` is None on a full run, so the replace is
    # repo-wide.
    drift = getattr(result, "doc_drift_report", None)
    if drift is not None:
        try:
            await replace_doc_drift_guarded(session, repo_id, drift)
        except Exception as exc:
            logger.warning("doc_drift_persist_skipped", error=str(exc))

    # ---- Health findings + per-file metrics ---------------------------------
    if getattr(result, "health_report", None):
        hr = result.health_report
        # Hoisted out of the coverage branch below: the same sha now stamps the
        # metric rows, so a reader can tell how far the health pass lags the
        # index instead of assuming the two moved together.
        head_sha = await _persist._analyzed_commit(session, repo_id)
        await _persist.save_full_health_report(
            session,
            repo_id,
            hr,
            analyzed_commit=head_sha,
            repo_path=getattr(result, "repo_path", None),
        )
        # Resolved coverage rows, when a report was ingested this run.
        coverage_files = getattr(hr, "coverage_files", None)
        if coverage_files:
            await save_coverage_files(
                session,
                repo_id,
                coverage_files,
                source_format=getattr(hr, "coverage_format", None) or "lcov",
                ingested_commit_sha=head_sha,
                provenance=getattr(hr, "coverage_provenance", None),
            )
        # Per-function blame rollup (FULL tier only; empty otherwise).
        fn_blame_rows = getattr(hr, "function_blame_rows", None)
        if fn_blame_rows:
            await upsert_git_function_blame_bulk(session, repo_id, fn_blame_rows)
        # The hotspot gate an incremental update will score against. Written
        # only by a run that walked the whole repo (the analyzer leaves it None
        # otherwise), so an incremental run cannot publish its changed-files
        # subset as the repo-wide percentile.
        fn_mod_p80 = getattr(hr, "repo_function_mod_p80", None)
        if fn_mod_p80:
            await set_repo_function_mod_p80(session, repo_id, fn_mod_p80)
        # Snapshot the run for trend tracking (rolling delete inside). From
        # the rows just written, by the same writer the update path uses.
        await _persist.snapshot_health_from_store(session, repo_id)

    # ---- Decision records ----------------------------------------------------
    # One contributor: the multi-source extractor. A second read used to fold
    # ``page.metadata["harvested_decisions"]`` in from LLM page generation, but
    # nothing ever wrote that key — harvesting only ever ran on ``file_page``,
    # and file pages are rendered structurally now, so the directive had no
    # host and the loop was permanently a no-op.
    decision_dicts: list[dict] = []
    if result.decision_report and result.decision_report.decisions:
        decision_dicts.extend(dataclasses.asdict(d) for d in result.decision_report.decisions)

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
        logger.debug("decision_unretire_skipped", error=str(_unretire_err))

    # One-shot drain of proposals left by retired extraction sources; without
    # this, DBs indexed before a removal keep a flooded review queue forever
    # (#751 for code_comment). Confirmed/dismissed rows are kept.
    try:
        from repowise.core.analysis.decision_provenance import RETIRED_SOURCES
        from repowise.core.persistence.crud import purge_proposed_decisions_by_source

        for _retired in RETIRED_SOURCES:
            await purge_proposed_decisions_by_source(session, repo_id, _retired)
    except Exception as _purge_err:
        logger.debug("decision_purge_skipped", error=str(_purge_err))

    # Re-stamp evidence rows left on a previous SOURCE_RANK ladder. Local stores
    # are created by ``init_db`` and never see Alembic, so the migration alone
    # would only reach hosted; this is the same repair on the path every store
    # takes. No-op scan once reconciled, and it runs whether or not this run
    # produced decisions, because a store with nothing new still holds the rows.
    try:
        from repowise.core.persistence.crud import reconcile_source_ranks

        await reconcile_source_ranks(session)
    except Exception as _rank_err:
        logger.debug("decision_rank_reconcile_skipped", error=str(_rank_err))

    # The same repair for the scoring formula, which leaves every input valid
    # and every stored score stale, so no filter can find it. Its own try, so
    # a failure here is not logged as the rank repair's.
    try:
        from repowise.core.persistence.crud import reconcile_decision_confidence

        await reconcile_decision_confidence(session)
    except Exception as _conf_err:
        logger.debug("decision_confidence_reconcile_skipped", error=str(_conf_err))

    # Move legacy records onto ids derived from their own identity, before
    # anything else reads or writes one. A random id is re-minted whenever a
    # store is rebuilt rather than updated, which strands every reference held
    # outside the row. Idempotent, and it never deletes a record.
    try:
        from repowise.core.persistence.decision_id_migration import apply_id_migration

        await apply_id_migration(
            session, repo_id, vector_store=getattr(result, "vector_store", None)
        )
    except Exception as _id_err:
        logger.debug("decision_id_migration_skipped", error=str(_id_err))

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
        logger.debug("decision_entity_migration_skipped", error=str(_migrate_err))

    if decision_dicts:
        # Reuse the run's shared vector store for semantic (paraphrase) dedup
        # and to make decisions searchable; title dedup still runs when None.
        store = getattr(result, "vector_store", None)
        touched_ids = await bulk_upsert_decisions(
            session,
            repo_id,
            decision_dicts,
            vector_store=store,
        )
        # Phase 3B: detect supersession/conflict among the just-upserted
        # decisions and record typed edges (auto-flipping the older only above
        # the high-confidence threshold). Heuristic-only here (no provider on
        # the persist path); the update path adds the gated LLM tiebreaker.
        if touched_ids and store is not None:
            try:
                from repowise.core.analysis.decision_evolution import (
                    detect_supersessions_and_conflicts,
                )

                evo = await detect_supersessions_and_conflicts(
                    session,
                    repo_id,
                    touched_ids=touched_ids,
                    vector_store=store,
                )
                if any(evo.values()):
                    logger.info("decision_supersession_detected", **evo)
            except Exception as _evo_err:
                logger.debug("supersession_detection_skipped", error=str(_evo_err))
        # Recompute staleness scores using git metadata.
        if result.git_metadata_list:
            try:
                git_meta_map: dict[str, dict] = {}
                for gm in result.git_metadata_list:
                    gm_dict = gm if isinstance(gm, dict) else dataclasses.asdict(gm)
                    fp = gm_dict.get("file_path", "")
                    if fp:
                        git_meta_map[fp] = gm_dict
                if git_meta_map:
                    updated = await recompute_decision_staleness(session, repo_id, git_meta_map)
                    if updated:
                        logger.info("decision_staleness_recomputed", updated=updated)
            except Exception as _stale_err:
                logger.debug("staleness_scoring_skipped", error=str(_stale_err))

    # Retire records whose commits were reverted. Outside the block above: a
    # new revert retires a decision stored by an earlier run just as well.
    try:
        from repowise.core.analysis.decisions.reverts import apply_revert_supersession

        await apply_revert_supersession(session, repo_id, getattr(result, "repo_path", None))
    except Exception as _revert_err:
        logger.debug("revert_supersession_skipped", error=str(_revert_err))
    try:
        from repowise.core.analysis.decisions.head_artifacts import apply_head_artifact_check

        await apply_head_artifact_check(session, repo_id, getattr(result, "repo_path", None))
    except Exception as _artifact_err:
        logger.debug("head_artifact_check_skipped", error=str(_artifact_err))

    # ---- Governance findings (additive pass, after decisions are persisted) ----
    # Runs after bulk_upsert_decisions + detect_supersessions_and_conflicts so
    # the decision graph is complete. Best-effort: never breaks persist.
    try:
        await _persist.refresh_governance_findings(session, repo_id)
    except Exception as _gov_err:
        logger.debug("governance_findings_skipped", error=str(_gov_err))
