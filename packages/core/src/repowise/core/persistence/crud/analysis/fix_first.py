"""Read the stores into the Fix-first queue.

The reads here only narrow; the row-to-item rule is the pure builder in
``repowise.core.analysis.health.fix_first.build``. Findings are read only for
files that can become an item or a counted exclusion: the files of worthwhile
opportunities and ready performance fixes, the files carrying the most
code-shape deduction, and the history-only files. Explanatory JSON is read
only for rows that can become an item, so the payload stays proportional to
the queue, not the repository.

Ceiling: plain finding items come only from the :data:`FINDING_FILES` files
with the most open code-shape deduction, so a file below that line never
becomes a finding item (a file with a plan or a performance fix still does).
Upgrade path: materialize each file's lead finding and its size at index
time, as refactoring and performance already are, and rank them in SQL.

The built queue is cached in process, keyed by the repository and the
newest write to each store it reads, so repeated calls between updates cost
one aggregate read.
"""

from __future__ import annotations

from collections import OrderedDict, namedtuple
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.finding_registry import excluded_types
from repowise.core.analysis.health.fix_first import DEFAULT_LIMIT, FixFirstQueue, build_fix_first
from repowise.core.analysis.health.fix_first.build import MIN_WORTH, hot_cut, hot_cut_offset
from repowise.core.analysis.health.perf.opportunity_rank import DEFAULT_QUEUE_STATES
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
                HealthFileMetric.code_origin,
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


#: One stand-in row per history-only file: the builder reads from it only
#: that the file's findings are all history, which is what it counts.
_HistoryOnly = namedtuple("_HistoryOnly", "file_path biomarker_type health_impact dimension")


async def _finding_files(session: AsyncSession, repo_id: str) -> tuple[set[str], list[Any]]:
    """The heaviest code-shape files, and one row for each history-only file."""
    f = HealthFinding
    shaped_impact = func.sum(
        case((f.biomarker_type.in_(history_biomarkers()), 0.0), else_=f.health_impact)
    )
    rows = (
        await session.execute(
            select(
                f.file_path,
                shaped_impact.label("shaped"),
                func.max(f.biomarker_type).label("marker"),
                func.max(f.health_impact).label("impact"),
            )
            .where(_eligible_findings(repo_id))
            .group_by(f.file_path)
        )
    ).all()
    heaviest = sorted((r for r in rows if r.shaped > 0), key=lambda r: (-r.shaped, r.file_path))
    history_only = [
        _HistoryOnly(r.file_path, r.marker, r.impact, "defect") for r in rows if not r.shaped
    ]
    return {r.file_path for r in heaviest[:FINDING_FILES]}, history_only


async def _findings(
    session: AsyncSession, repo_id: str, full: set[str], planned: set[str], functions: set[str]
) -> list[Any]:
    """Every eligible finding in ``full``; in ``planned`` only what an item quotes.

    A file with a plan that becomes an item needs its history markers (the
    item's context) and the findings on the function the plan changes (its
    numbers); its other findings never lead, because the plan speaks for it.
    """
    if not full and not planned:
        return []
    f = HealthFinding
    history = history_biomarkers()
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
                case((f.biomarker_type.in_(history), None), else_=f.details_json).label(
                    "details_json"
                ),
            ).where(
                _eligible_findings(repo_id),
                or_(
                    f.file_path.in_(full),
                    and_(
                        f.file_path.in_(planned - full),
                        or_(f.biomarker_type.in_(history), f.function_name.in_(functions)),
                    ),
                ),
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
    # Details are decoded only for a cause the builder can make an item of.
    ready = and_(
        p.actionability_state.in_(DEFAULT_QUEUE_STATES),
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


def _decoded(rows: list[Any]) -> list[Any]:
    """``details_json`` decoded once into ``details``, which the builder reads first."""
    if not rows:
        return rows
    fields = [("details" if f == "details_json" else f) for f in rows[0]._fields]
    shape = namedtuple("Row", fields)  # type: ignore[misc]
    out = []
    for r in rows:
        values = list(r)
        at = fields.index("details")
        values[at] = detail_map(r) if r.details_json else None
        out.append(shape(*values))
    return out


def _steps(refactoring: list[Any]) -> list[dict[str, Any]]:
    """Every step of the opportunities that can become an item."""
    return [s for r in refactoring if r.details for s in r.details.get("steps") or []]


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


#: Built queues kept in process. A handful covers the shapes one surface asks
#: for (dashboard, CLI, Do next, one id), per repository a server holds.
CACHE_SIZE = 32
_cache: OrderedDict[tuple[Any, ...], FixFirstQueue] = OrderedDict()


async def _stamp(session: AsyncSession, repo_id: str) -> tuple[Any, ...]:
    """The newest write and the row count of every store the queue reads.

    A rewrite, a triage change (``updated_at`` moves) or a deletion (the
    count moves) changes it; git and graph rows are rewritten with the
    health rows that read them.
    """
    stamps: list[Any] = []
    for model in (HealthFileMetric, HealthFinding, RefactoringOpportunity, PerformanceOpportunity):
        stamps.extend(
            (
                await session.execute(
                    select(func.max(model.updated_at), func.count()).where(
                        model.repository_id == repo_id
                    )
                )
            ).one()
        )
    return tuple(stamps)


def clear_fix_first_cache() -> None:
    _cache.clear()


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
    key = (
        str(session.bind.url) if session.bind is not None else None,
        repository_id,
        limit,
        scope,
        item_id,
        await _stamp(session, repository_id),
    )
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return cached
    queue = await _build(session, repository_id, limit=limit, scope=scope, item_id=item_id)
    _cache[key] = queue
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return queue


async def _build(
    session: AsyncSession,
    repository_id: str,
    *,
    limit: int | None,
    scope: str,
    item_id: str | None,
) -> FixFirstQueue:
    refactoring = _decoded(await _refactoring(session, repository_id))
    performance = _decoded(await _performance(session, repository_id))
    steps = _steps(refactoring)
    heavy, history_only = await _finding_files(session, repository_id)
    # Ceiling: a plan whose details carry no steps is not an item, so its
    # file is read in full like any other.
    planned = {r.file_path for r in refactoring if r.details and r.details.get("steps")}
    full = heavy | {p.file_path for p in performance if p.details} | (
        {r.file_path for r in refactoring if r.details} - planned
    )
    functions = {s["target_symbol"] for s in steps if s.get("target_symbol")}
    findings = await _findings(session, repository_id, full, planned, functions)
    paths = full | planned | {r.file_path for r in history_only}
    return build_fix_first(
        metrics=await _metrics(session, repository_id, paths),
        findings=[*findings, *(r for r in history_only if r.file_path not in full | planned)],
        refactoring=refactoring,
        performance=performance,
        plans=await _plans(session, repository_id, steps),
        limit=limit,
        scope=scope,
        item_id=item_id,
        basis=await _basis(session, repository_id),
        hot_cuts=await _hot_cuts(session, repository_id),
    )


__all__ = ["CACHE_SIZE", "FINDING_FILES", "clear_fix_first_cache", "load_fix_first"]
