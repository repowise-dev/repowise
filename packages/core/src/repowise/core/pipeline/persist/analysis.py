"""Analysis-phase persistence: dead code, drift, health and decisions."""

from __future__ import annotations

import dataclasses
from typing import Any

import structlog

# Patchable steps are looked up on the package at call time, so a patch on
# ``repowise.core.pipeline.persist`` reaches this module too.
from repowise.core.pipeline import persist as _persist

from .decisions import apply_decision_lifecycle, repair_decision_store, store_decisions

logger = structlog.get_logger("repowise.core.pipeline.persist")


async def persist_analysis(result: Any, session: Any, repo_id: str) -> None:
    """Persist analysis-phase outputs: dead code, health, decisions, drift.

    Dead-code, health and doc-drift writes are repo-wide DELETE-THEN-INSERT (so
    they converge on re-run but don't support partial-within-phase resume);
    decisions/governance are idempotent. Intended to run once the analysis
    phase has fully completed.
    """
    from repowise.core.analysis.health.coverage import measurement_commit
    from repowise.core.persistence.crud import (
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
            prov = getattr(hr, "coverage_provenance", None)
            # A report found on disk measures the tree as of its own write
            # time, not of HEAD (#3901); HEAD is right only for a report
            # written since the last commit, which measurement_commit also
            # answers. ``ingested_at`` stays the ingest time.
            await save_coverage_files(
                session,
                repo_id,
                coverage_files,
                source_format=getattr(hr, "coverage_format", None) or "lcov",
                ingested_commit_sha=measurement_commit(
                    await _persist._analyzed_repo_path(session, repo_id),
                    getattr(prov, "measured_at", None),
                    head_sha,
                ),
                provenance=prov,
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

    vector_store = getattr(result, "vector_store", None)
    await repair_decision_store(session, repo_id, vector_store=vector_store)
    if decision_dicts:
        await store_decisions(
            session,
            repo_id,
            decision_dicts,
            vector_store=vector_store,
            git_metadata=result.git_metadata_list,
            repo_path=getattr(result, "repo_path", None),
        )
    await apply_decision_lifecycle(session, repo_id, getattr(result, "repo_path", None))

    # ---- Governance findings (additive pass, after decisions are persisted) ----
    # Runs after bulk_upsert_decisions + detect_supersessions_and_conflicts so
    # the decision graph is complete. Best-effort: never breaks persist.
    try:
        await _persist.refresh_governance_findings(session, repo_id)
    except Exception as _gov_err:
        logger.debug("governance_findings_skipped", error=str(_gov_err))
