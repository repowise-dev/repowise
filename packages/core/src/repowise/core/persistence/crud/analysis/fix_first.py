"""Read the stores into the Fix-first queue.

The reads here only narrow; the row-to-item rule is the pure builder in
``repowise.core.analysis.health.fix_first.build``. Findings are read only for
files that can become an item or a counted exclusion: the files of worthwhile
opportunities and ready performance fixes, the files carrying the most
code-shape deduction, and the history-only files. Explanatory JSON is read
only for rows that can become an item, so the payload stays proportional to
the queue, not the repository.
"""

from __future__ import annotations

from collections import namedtuple
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.finding_registry import excluded_types
from repowise.core.analysis.health.fix_first import DEFAULT_LIMIT, FixFirstQueue, build_fix_first
from repowise.core.analysis.health.fix_first.build import MIN_WORTH, hot_cut, hot_cut_offset
from repowise.core.analysis.health.rows import detail_map
from repowise.core.analysis.health.scoring import history_biomarkers

from ...models import (
    GitMetadata,
    GraphMetric,
    HealthFileMetric,
    HealthFinding,
    PerformanceOpportunity,
    RefactoringOpportunity,
    RefactoringSuggestion,
)

#: Files read for plan-less finding items, by open code-shape deduction.
#: Ceiling: a file past this rank never becomes a finding item. The queue
#: shows ten, and finding items rank below plans of the same value.
FINDING_FILES = 100


def _plain(result: Any) -> list[Any]:
    """Rows as named tuples: the builder reads each field many times, and a
    tuple attribute is several times cheaper than a SQL row's."""
    rows = result.all()
    if not rows:
        return []
    shape = namedtuple("Row", rows[0]._fields)  # type: ignore[misc]
    return [shape(*r) for r in rows]


async def _metrics(session: AsyncSession, repo_id: str, paths: set[str]) -> list[Any]:
    if not paths:
        return []
    return _plain(
        await session.execute(
            select(
                HealthFileMetric.file_path,
                HealthFileMetric.nloc,
                HealthFileMetric.is_test,
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
            .where(
                HealthFileMetric.repository_id == repo_id, HealthFileMetric.file_path.in_(paths)
            )
        )
    )


async def _hot_cuts(session: AsyncSession, repo_id: str) -> tuple[float, float]:
    """The builder's hot-file thresholds over every production file.

    Two integer columns per file, so the whole population costs one narrow read
    while the builder sees full rows for the candidates only.
    """
    m = HealthFileMetric
    rows = (
        await session.execute(
            select(
                func.coalesce(GitMetadata.commit_count_90d, 0),
                func.coalesce(GraphMetric.in_degree, 0),
            )
            .select_from(m)
            .outerjoin(
                GitMetadata,
                (GitMetadata.repository_id == m.repository_id)
                & (GitMetadata.file_path == m.file_path),
            )
            .outerjoin(
                GraphMetric,
                (GraphMetric.repository_id == m.repository_id) & (GraphMetric.node_id == m.file_path),
            )
            .where(m.repository_id == repo_id, or_(m.is_test.is_(None), m.is_test.is_(False)))
        )
    ).all()
    if not rows:
        return hot_cut(None), hot_cut(None)
    at = hot_cut_offset(len(rows))
    churn, deps = (sorted(column) for column in zip(*rows, strict=True))
    return hot_cut(churn[at]), hot_cut(deps[at])


async def _basis(session: AsyncSession, repo_id: str) -> dict[str, str | None]:
    row = (
        await session.execute(
            select(HealthFileMetric.updated_at, HealthFileMetric.analyzed_commit)
            .where(
                HealthFileMetric.repository_id == repo_id,
                HealthFileMetric.updated_at.is_not(None),
            )
            .order_by(HealthFileMetric.updated_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return {"analyzed_commit": None, "health_analyzed_at": None}
    return {"analyzed_commit": row.analyzed_commit, "health_analyzed_at": row.updated_at.isoformat()}


def _eligible_findings(repo_id: str) -> Any:
    """Open, scoring, code-health findings a surface may show."""
    f = HealthFinding
    return and_(
        f.repository_id == repo_id,
        f.status == "open",
        f.health_impact > 0,
        or_(f.dimension.is_(None), f.dimension != "performance"),
        f.biomarker_type.not_in(excluded_types()),
    )


async def _finding_paths(session: AsyncSession, repo_id: str) -> set[str]:
    """The heaviest code-shape files, and every file whose findings are all history."""
    f = HealthFinding
    shaped_impact = func.sum(
        case((f.biomarker_type.in_(history_biomarkers()), 0.0), else_=f.health_impact)
    )
    rows = (
        await session.execute(
            select(f.file_path, shaped_impact.label("shaped"))
            .where(_eligible_findings(repo_id))
            .group_by(f.file_path)
        )
    ).all()
    heaviest = sorted((r for r in rows if r.shaped > 0), key=lambda r: (-r.shaped, r.file_path))
    return {r.file_path for r in heaviest[:FINDING_FILES]} | {
        r.file_path for r in rows if not r.shaped
    }


async def _findings(
    session: AsyncSession, repo_id: str, paths: set[str], quoted: set[str], functions: set[str]
) -> list[Any]:
    """Every eligible finding in ``paths``, with the numbers only where a sentence quotes them.

    Details travel for files whose own finding may lead (``quoted``) and for
    the functions a plan changes; elsewhere the row's presence is what counts.
    """
    if not paths:
        return []
    f = HealthFinding
    shaped = f.biomarker_type.not_in(history_biomarkers())
    return _plain(
        await session.execute(
            select(
                f.file_path,
                f.biomarker_type,
                f.severity,
                f.function_name,
                f.line_start,
                f.line_end,
                f.reason,
                f.health_impact,
                f.public_id,
                f.dimension,
                f.status,
                # Only a code-shape finding's numbers are quoted.
                case(
                    (
                        and_(
                            shaped,
                            or_(f.file_path.in_(quoted), f.function_name.in_(functions)),
                        ),
                        f.details_json,
                    )
                ).label("details_json"),
            ).where(_eligible_findings(repo_id), f.file_path.in_(paths))
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


def _steps(refactoring: list[Any]) -> list[dict[str, Any]]:
    """Every step of the opportunities that can become an item."""
    return [s for r in refactoring if r.details_json for s in detail_map(r).get("steps") or []]


async def _plans(session: AsyncSession, repo_id: str, steps: list[dict[str, Any]]) -> list[Any]:
    """The plans those steps name: span, signature, evidence."""
    ids = {s.get("plan_id") for s in steps if s.get("plan_id")}
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
    item_id: str | None = None,
) -> FixFirstQueue:
    """The Fix-first queue for one repository, from its stored analysis.

    ``item_id`` keeps only that item, at its rank, for a lookup by id.
    """
    refactoring = await _refactoring(session, repository_id)
    performance = await _performance(session, repository_id)
    steps = _steps(refactoring)
    paths = await _finding_paths(session, repository_id)
    quoted = set(paths)
    paths |= {r.file_path for r in refactoring if r.details_json}
    paths |= {p.file_path for p in performance if p.details_json}
    functions = {s["target_symbol"] for s in steps if s.get("target_symbol")}
    return build_fix_first(
        metrics=await _metrics(session, repository_id, paths),
        findings=await _findings(session, repository_id, paths, quoted, functions),
        refactoring=refactoring,
        performance=performance,
        plans=await _plans(session, repository_id, steps),
        limit=limit,
        scope=scope,
        item_id=item_id,
        basis=await _basis(session, repository_id),
        hot_cuts=await _hot_cuts(session, repository_id),
    )


__all__ = ["FINDING_FILES", "load_fix_first"]
