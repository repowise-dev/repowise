"""Health report writers and the governance findings refresh."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

# Patchable steps are looked up on the package at call time, so a patch on
# ``repowise.core.pipeline.persist`` reaches this module too.
from repowise.core.pipeline import persist as _persist

from .prune import path_liveness

logger = structlog.get_logger("repowise.core.pipeline.persist")


async def _analyzed_repo_path(session: Any, repo_id: str) -> Path | None:
    """The working tree the health pass scored, read from the repository row.

    The same source :func:`_analyzed_commit` takes HEAD from, so a commit stamp
    and the tree it names cannot disagree. The pipeline result is not that
    source: the resume checkpoint hands ``persist_analysis`` a view of the
    reports only, which carries no path.
    """
    from repowise.core.persistence.models import Repository

    try:
        repo = await session.get(Repository, repo_id)
        local_path = getattr(repo, "local_path", None) if repo else None
        return Path(local_path) if local_path else None
    except Exception:
        return None


async def _analyzed_commit(session: Any, repo_id: str) -> str | None:
    """Live HEAD of the repo being indexed, for stamping health rows.

    Read off disk rather than from ``Repository.head_commit``: the health pass
    just scored the working tree, and the stored column is written by a
    different step whose ordering relative to this one is not guaranteed.
    ``None`` on any failure — an unstamped row reads as "not recorded", which
    is honest, while a wrong sha would not be. Both index paths read it here;
    the pipeline result carries no sha, so the full path used to stamp ``NULL``.
    """
    from repowise.core.workspace.update import get_head_commit

    local_path = await _analyzed_repo_path(session, repo_id)
    return get_head_commit(local_path) if local_path is not None else None


async def snapshot_health_from_store(session: Any, repo_id: str) -> None:
    """Append a ``HealthSnapshot`` built from the repository's stored rows.

    One writer for the full index and the incremental update. A partial report
    cannot be snapshotted directly, because it holds only the changed files and
    a snapshot has to describe the whole repository; the store after the write
    holds exactly that, on both paths.

    Best-effort: a snapshot that fails to write is logged and never fails the
    run that produced the rows it describes.
    """
    from sqlalchemy import select

    from repowise.core.analysis.health.ranking import deduction_by_path
    from repowise.core.analysis.health.scoring import compute_kpis
    from repowise.core.analysis.health.trends import snapshot_fields
    from repowise.core.persistence.crud import get_hotspot_file_paths, save_health_snapshot
    from repowise.core.persistence.models import HealthFileMetric, HealthFinding

    try:
        metrics = list(
            (
                await session.execute(
                    select(HealthFileMetric).where(HealthFileMetric.repository_id == repo_id)
                )
            )
            .scalars()
            .all()
        )
        if not metrics:
            return
        findings = list(
            (
                await session.execute(
                    select(HealthFinding).where(
                        HealthFinding.repository_id == repo_id,
                        HealthFinding.status == "open",
                    )
                )
            )
            .scalars()
            .all()
        )
        kpis = compute_kpis(
            metrics,
            await get_hotspot_file_paths(session, repo_id),
            deductions=deduction_by_path(findings),
        )
        fields = snapshot_fields(kpis, metrics, findings)
        if fields is not None:
            await save_health_snapshot(session, repo_id, **fields)
    except Exception as exc:
        logger.warning("health_snapshot_skipped", error=str(exc))


async def save_full_health_report(
    session: Any,
    repo_id: str,
    health_report: Any,
    *,
    analyzed_commit: str | None,
    repo_path: Any = None,
) -> None:
    """Full-replace a repository's health rows and everything derived from them.

    Both callers that re-score every file land here: the analysis phase of an
    index, and the update command's periodic/config-triggered re-score. The
    metrics, the findings, the non-performance refactoring suggestions and the
    two materialized read models move together, so a re-score cannot leave the
    queues describing findings that were deleted out from under them.

    ``analyzed_commit`` stamps the metric rows and the read models with the
    commit these scores were computed against; ``None`` reads as "not
    recorded", which is honest, while a wrong sha would not be. ``repo_path``
    lets a plan whose file is gone read as deleted.
    """
    from repowise.core.persistence.crud import (
        PayoffContext,
        finalize_performance_opportunities,
        finalize_refactoring_opportunities,
        save_health_findings,
        save_health_metrics,
        save_refactoring_suggestions,
        write_function_facts,
    )

    hr = health_report
    metrics = list(getattr(hr, "metrics", None) or [])
    findings = list(getattr(hr, "findings", None) or [])
    if not metrics and not findings:
        # A pass that scored nothing is a broken pass, not a clean repository.
        # Every write below replaces the repository's rows wholesale, so
        # honouring an empty report would delete every metric, resolve every
        # open plan a person has triaged, and empty both queues on the strength
        # of a parse failure or an exclude pattern that matched everything.
        return
    await save_health_metrics(session, repo_id, metrics, analyzed_commit=analyzed_commit)
    # One savepoint over the findings and everything derived from them, so a
    # rebuild that failed halfway cannot leave the queue describing findings
    # that were never stored. It runs on an empty finding set too: the queue
    # has to be reconciled to nothing just the same.
    async with session.begin_nested():
        if findings:
            await save_health_findings(session, repo_id, findings)
        # Structured refactoring suggestions (Extract Class, ...). Repo-wide
        # reconciliation: an unchanged plan keeps its id and its triage state,
        # and one nobody detects any more resolves rather than disappearing.
        # Performance plans are excluded: the finalizer below owns them, so
        # they are generated once, from the merged stored findings, by the same
        # writer on every path.
        await save_refactoring_suggestions(
            session,
            repo_id,
            [
                suggestion
                for suggestion in (getattr(hr, "refactoring_suggestions", None) or [])
                if getattr(suggestion, "refactoring_type", None) != "performance_fix"
            ],
            # Before the facts below replace the stored ones: those are the
            # measures a resolved plan was detected against.
            payoff=PayoffContext(
                fact_rows=getattr(hr, "function_facts", None),
                is_live=path_liveness(repo_path),
                commit=analyzed_commit,
            ),
        )
        # No rows means no graph to key them on: the stored ones stay. Before
        # the finalizers, which read each plan's nearest tests off these rows.
        fact_rows = getattr(hr, "function_facts", None)
        if fact_rows is not None:
            await write_function_facts(
                session,
                repo_id,
                fact_rows,
                roles=getattr(hr, "execution_roles", None),
                tests=getattr(hr, "function_tests", None),
            )
        # Group the stored performance findings into the materialized causal
        # read model, then reconcile lifecycle and plans.
        await finalize_performance_opportunities(
            session,
            repo_id,
            analyzed_commit=analyzed_commit,
            plan_policy=getattr(hr, "performance_plan_policy", None),
            execution_roles=getattr(hr, "execution_roles", None),
        )
        # Fold the plans just written into per-file opportunities. Last in the
        # savepoint because it composes over the stored rows, so it has to see
        # the reconciliation above rather than the detector output.
        await finalize_refactoring_opportunities(session, repo_id, analyzed_commit=analyzed_commit)


async def refresh_governance_findings(
    session: Any, repo_id: str, *, recompose_queue: bool = True
) -> int:
    """Rebuild the governance findings from the stored decisions and health rows.

    Every writer that runs the governance pass comes through here: the index
    after its decisions are stored, the update after its decision steps, and
    the full health re-score, whose findings replace deletes these rows along
    with every other open finding. Returns the number of findings written.

    ``recompose_queue`` re-folds the refactoring queue when findings were
    written, since it is composed over the stored findings and may have been
    built before these rows existed. Only that queue: the performance
    finalizer reads ``performance`` findings, and every governance biomarker
    is ``organizational``. A caller that finalizes the queue next anyway
    passes ``False``.
    """
    from sqlalchemy import select

    from repowise.core.analysis.health.governance import build_governance_findings
    from repowise.core.persistence.crud import (
        finalize_refactoring_opportunities,
        get_decision_health_summary,
        get_scored_file_paths,
        replace_governance_findings,
    )
    from repowise.core.persistence.models import DecisionRecord

    # One savepoint: the replace deletes before it inserts, and the queue
    # reconciles as a whole, so a failure must leave neither half-written.
    async with session.begin_nested():
        decisions = list(
            (
                await session.execute(
                    select(DecisionRecord).where(DecisionRecord.repository_id == repo_id)
                )
            )
            .scalars()
            .all()
        )
        findings = build_governance_findings(
            health_summary=await get_decision_health_summary(session, repo_id),
            decisions=decisions,
            scored_paths=await get_scored_file_paths(session, repo_id),
        )
        await replace_governance_findings(session, repo_id, findings)
        if findings and recompose_queue:
            await finalize_refactoring_opportunities(
                session, repo_id, analyzed_commit=await _persist._analyzed_commit(session, repo_id)
            )
    if findings:
        logger.info("governance_findings_persisted", repo_id=repo_id, count=len(findings))
    return len(findings)
