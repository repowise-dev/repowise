"""The stored-analysis freshness block get_health attaches to ``_meta``."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from repowise.core.analysis.health.semantics import health_semantics_contract
from repowise.core.persistence.models import HealthFileMetric


def _attach_health_analysis_meta(
    meta: dict[str, Any], metrics: list[HealthFileMetric]
) -> None:
    """Keep stored analysis distinct from index/live Git verification."""
    analyzed = [m for m in metrics if getattr(m, "updated_at", None)]
    latest = max(analyzed, key=lambda m: m.updated_at) if analyzed else None
    _write_health_analysis_meta(
        meta,
        has_metrics=bool(metrics),
        latest_at=latest.updated_at if latest is not None else None,
        latest_commit=getattr(latest, "analyzed_commit", None) if latest else None,
        distinct_commits=len({c for m in analyzed if (c := getattr(m, "analyzed_commit", None))}),
    )


async def _attach_repository_analysis_meta(
    session: Any, repository: Any, meta: dict[str, Any]
) -> None:
    """The same block, computed over the repository rather than one file.

    Whether the analysis recorded its commit is a fact about the repository, so
    every mode must answer it from the repo-wide rows, not the rows it reports
    on, or a scoped call and the dashboard disagree. Bounded aggregates, no scan.
    """
    latest = (
        await session.execute(
            select(HealthFileMetric.updated_at, HealthFileMetric.analyzed_commit)
            .where(
                HealthFileMetric.repository_id == repository.id,
                HealthFileMetric.updated_at.is_not(None),
            )
            .order_by(HealthFileMetric.updated_at.desc())
            .limit(1)
        )
    ).first()
    total, distinct = (
        await session.execute(
            select(
                func.count(),
                func.count(func.distinct(HealthFileMetric.analyzed_commit)),
            ).where(HealthFileMetric.repository_id == repository.id)
        )
    ).one()
    _write_health_analysis_meta(
        meta,
        has_metrics=total > 0,
        latest_at=latest[0] if latest else None,
        latest_commit=latest[1] if latest else None,
        distinct_commits=distinct,
    )


def _write_health_analysis_meta(
    meta: dict[str, Any],
    *,
    has_metrics: bool,
    latest_at: Any,
    latest_commit: str | None,
    distinct_commits: int,
) -> None:
    """The one place the analysis-freshness block is shaped."""
    meta["health_semantics"] = health_semantics_contract()
    metrics = has_metrics
    analyzed = latest_at is not None
    commits_count = distinct_commits
    # Metrics without a recorded commit are usable but unattributable: a
    # provenance gap, not degradation. ``degraded`` is reserved tool-wide for a
    # capability that failed or was unavailable.
    status = (
        "available" if latest_commit else "provenance_unknown" if metrics else "unavailable"
    )
    analysis: dict[str, Any] = {
        "status": status,
        "source": "stored_health_analysis",
        "recomputed_this_call": False,
        "live_verification": {
            "basis": (
                "index_commit_and_live_git_head"
                if meta.get("indexed_commit") and meta.get("live_head")
                else "unavailable"
            ),
            "source_bytes_verified": False,
        },
        "refresh": {
            "command": "repowise update",
            "precondition": "commit health-relevant working-tree changes first",
            "required_before_comparison": True,
        },
    }
    if not metrics:
        analysis["reason"] = "no_stored_health_metrics"
    if analyzed:
        analyzed_at = latest_at.isoformat()
        analysis["analyzed_at"] = analyzed_at
        meta["health_analyzed_at"] = analyzed_at
        if latest_commit:
            analyzed_commit = latest_commit[:12]
            analysis["analyzed_commit"] = analyzed_commit
            meta["health_analyzed_commit"] = analyzed_commit
        if commits_count > 1:
            analysis["analyzed_commits_distinct"] = commits_count
            meta["health_analyzed_commits_distinct"] = commits_count
        if not latest_commit:
            analysis["reason"] = "analysis_commit_not_recorded"
            analysis["analyzed_commit"] = None
    else:
        analysis["recorded_at"] = None
        analysis["recorded_commit"] = None
        if metrics:
            analysis["reason"] = "analysis_timestamp_not_recorded"
    meta["health_analysis"] = analysis
