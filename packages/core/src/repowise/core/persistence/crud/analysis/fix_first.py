"""Read the stores into the Fix-first queue.

The reads here only narrow; the row-to-item rule is the pure builder in
``repowise.core.analysis.health.fix_first.build``. Explanatory JSON is read
only for rows that can become an item (over the minimum worth, or with a
plan), so the payload stays proportional to the queue, not the repository.
"""

from __future__ import annotations

from collections import namedtuple
from typing import Any

from sqlalchemy import and_, case, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.fix_first import DEFAULT_LIMIT, FixFirstQueue, build_fix_first
from repowise.core.analysis.health.fix_first.build import MIN_WORTH
from repowise.core.analysis.health.rows import detail_map

from ...models import (
    GitMetadata,
    GraphMetric,
    HealthFileMetric,
    HealthFinding,
    PerformanceOpportunity,
    RefactoringOpportunity,
    RefactoringSuggestion,
)


def _plain(result: Any) -> list[Any]:
    """Rows as named tuples: the builder reads each field many times, and a
    tuple attribute is several times cheaper than a SQL row's."""
    rows = result.all()
    if not rows:
        return []
    shape = namedtuple("Row", rows[0]._fields)  # type: ignore[misc]
    return [shape(*r) for r in rows]


async def _metrics(session: AsyncSession, repo_id: str) -> list[Any]:
    return _plain(
        await session.execute(
            select(
                HealthFileMetric.file_path,
                HealthFileMetric.score,
                HealthFileMetric.nloc,
                HealthFileMetric.is_test,
                HealthFileMetric.analyzed_commit,
                HealthFileMetric.updated_at,
                GitMetadata.commit_count_90d,
                GraphMetric.in_degree.label("dependents"),
            )
            .outerjoin(
                GitMetadata,
                (GitMetadata.repository_id == HealthFileMetric.repository_id)
                & (GitMetadata.file_path == HealthFileMetric.file_path),
            )
            .outerjoin(
                GraphMetric,
                (GraphMetric.repository_id == HealthFileMetric.repository_id)
                & (GraphMetric.node_id == HealthFileMetric.file_path),
            )
            .where(HealthFileMetric.repository_id == repo_id)
        )
    )


async def _findings(session: AsyncSession, repo_id: str) -> list[Any]:
    return _plain(
        await session.execute(
            select(
                HealthFinding.file_path,
                HealthFinding.biomarker_type,
                HealthFinding.severity,
                HealthFinding.function_name,
                HealthFinding.line_start,
                HealthFinding.line_end,
                HealthFinding.reason,
                HealthFinding.health_impact,
                HealthFinding.public_id,
                HealthFinding.dimension,
                HealthFinding.status,
            ).where(
                HealthFinding.repository_id == repo_id,
                HealthFinding.status == "open",
                HealthFinding.health_impact > 0,
            )
        )
    )


async def _refactoring(session: AsyncSession, repo_id: str) -> list[Any]:
    o = RefactoringOpportunity
    return _plain(
        await session.execute(
            select(
                o.opportunity_id,
                o.rank_position,
                o.rank_score,
                o.file_path,
                o.lead_biomarker,
                o.lead_refactoring_type,
                o.effort_bucket,
                o.confidence,
                o.affected_files_total,
                o.recoverable_health,
                o.status,
                case((o.recoverable_health >= MIN_WORTH, o.details_json)).label("details_json"),
            )
            .where(o.repository_id == repo_id, o.status == "open")
            .order_by(o.rank_position)
        )
    )


async def _performance(session: AsyncSession, repo_id: str) -> list[Any]:
    p = PerformanceOpportunity
    ready = and_(
        p.actionability_state != "expected",
        p.plan_state == "available",
        p.fix_strategy.is_not(None),
    )
    return _plain(
        await session.execute(
            select(
                p.opportunity_id,
                p.rank_position,
                p.rank_score,
                p.execution_context,
                p.boundary_kind,
                p.biomarker_type,
                p.actionability_state,
                p.plan_state,
                p.fix_strategy,
                p.fix_safety,
                p.file_path,
                p.intervention_symbol,
                p.terminal_sink,
                p.affected_call_sites_total,
                p.affected_files_total,
                p.status,
                case((ready, p.details_json)).label("details_json"),
            )
            .where(p.repository_id == repo_id, p.status == "open")
            .order_by(p.rank_position)
        )
    )


async def _plans(session: AsyncSession, repo_id: str, refactoring: list[Any]) -> list[Any]:
    """Extract-method plans the worthwhile opportunities name, for their size."""
    ids = {
        s.get("plan_id")
        for r in refactoring
        if r.details_json
        for s in detail_map(r).get("steps") or []
        if s.get("refactoring_type") == "extract_method" and s.get("plan_id")
    }
    if not ids:
        return []
    return _plain(
        await session.execute(
            select(
                RefactoringSuggestion.public_id,
                RefactoringSuggestion.evidence_json,
                RefactoringSuggestion.plan_json,
            ).where(
                RefactoringSuggestion.repository_id == repo_id,
                RefactoringSuggestion.public_id.in_(ids),
            )
        )
    )


async def load_fix_first(
    session: AsyncSession,
    repository_id: str,
    *,
    limit: int | None = DEFAULT_LIMIT,
    scope: str = "production",
) -> FixFirstQueue:
    """The Fix-first queue for one repository, from its stored analysis."""
    refactoring = await _refactoring(session, repository_id)
    return build_fix_first(
        metrics=await _metrics(session, repository_id),
        findings=await _findings(session, repository_id),
        refactoring=refactoring,
        performance=await _performance(session, repository_id),
        plans=await _plans(session, repository_id, refactoring),
        limit=limit,
        scope=scope,
    )


__all__ = ["load_fix_first"]
