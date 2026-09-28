"""Every read one get_health call needs, gated on the blocks that will use it.

``load_health_data`` runs inside the caller's session and returns plain rows;
nothing after it touches the database except the analysis-freshness block.
Each reader returns its empty value when its block was not asked for, so the
projection gates the work as well as the payload.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from repowise.core.analysis.doc_drift.constants import UNAVAILABLE_NO_TABLE
from repowise.core.analysis.health.churn_complexity import churn_complexity_points
from repowise.core.analysis.health.grading import TARGET_SCORE
from repowise.core.analysis.health.perf.coverage import PerfCoverage, coverage_for_metrics
from repowise.core.analysis.health.ranking import deduction_by_path, sort_metrics_worst_first
from repowise.core.analysis.health.refactoring.recommendations import (
    Recommendation,
    hydrate_recommendations,
)
from repowise.core.analysis.health.signals import file_signals
from repowise.core.analysis.health.trends import project_scope
from repowise.core.ingestion.models import FILE_DEPENDENCY_EDGE_TYPES
from repowise.core.persistence.crud import (
    get_all_git_metadata,
    get_coverage_summary,
    get_doc_drift_findings,
    get_file_language_map,
    get_git_metadata_bulk,
    get_hotspot_file_paths,
    get_node_degree_counts_bulk,
    get_refactoring_suggestions,
    get_test_file_paths,
    list_health_snapshots,
    load_coverage_for_repo,
)
from repowise.core.persistence.models import HealthFileMetric, RefactoringSuggestion
from repowise.server.mcp_server._helpers import filter_rows_by_attr
from repowise.server.mcp_server.tool_health.findings import (
    FindingSets,
    load_dashboard_findings,
    load_targeted_findings,
    read_accuracy_rows,
)
from repowise.server.mcp_server.tool_health.pillars import (
    _performance_blocks,
    _PerformanceBlocks,
    _refactoring_blocks,
    _RefactoringBlocks,
)
from repowise.server.mcp_server.tool_health.population import Population, load_population
from repowise.server.mcp_server.tool_health.request import HealthRequest
from repowise.server.mcp_server.tool_health.summary import _DIRECTIVE_CANDIDATES, _leads_by_file
from repowise.server.services.performance_health import PerformanceHealthService
from repowise.server.services.refactoring_health import RefactoringHealthService, plan_view


@dataclass
class HealthData:
    """The rows one response is built from, read inside a single session."""

    repository: Any
    reference_repository: str
    pop: Population
    test_paths: set[str]
    findings: FindingSets
    deductions: dict[str, float]
    metric_rows: list[HealthFileMetric]
    refactoring: _RefactoringBlocks
    performance: _PerformanceBlocks
    by_leverage: list[HealthFileMetric]
    leads: dict[str, dict[str, Any]]
    hotspot_paths: set[str] = field(default_factory=set)
    perf_coverage: PerfCoverage | None = None
    perf_findings_count: int = 0
    accuracy_rows: list[Any] = field(default_factory=list)
    refactoring_rows: list[Any] = field(default_factory=list)
    refactoring_recommendations: list[Recommendation] = field(default_factory=list)
    coverage_rows: list[Any] = field(default_factory=list)
    coverage_summary: dict[str, Any] = field(default_factory=dict)
    signals_by_path: dict[str, dict[str, Any]] = field(default_factory=dict)
    drift_rows: list[Any] = field(default_factory=list)
    drift_unavailable: str | None = None
    churn_points: list[dict[str, Any]] = field(default_factory=list)
    snapshots: list[Any] = field(default_factory=list)
    plan_biomarkers_by_path: dict[str, set[str]] = field(default_factory=dict)
    plan_count_by_path: dict[str, int] = field(default_factory=dict)


async def load_health_data(
    session: Any, repository: Any, reference_repository: str, repo_path: Any, req: HealthRequest
) -> HealthData:
    """Run every read the requested blocks need, in one session."""
    pop = await load_population(session, repository, repo_path, req)
    test_paths = await _read_test_paths(session, repository, pop, req)
    if pop.scoped:
        findings = await load_targeted_findings(session, repository, pop, req)
    else:
        findings = await load_dashboard_findings(session, repository, pop, req, test_paths)

    # Same worst-first comparator as the crud layer, so REST and this tool
    # agree on the worst file. From ``lead_rows``, the unfiltered open set:
    # viewing one dimension must not change which files are worst.
    deductions = deduction_by_path(findings.lead_rows)
    # Rebound before every reader: ``min()`` and stable sorts break ties by
    # input order, so every block must see the same order as ``worst_files``.
    pop.all_metrics = sort_metrics_worst_first(pop.all_metrics, deductions)
    targets = set(pop.effective_targets)
    metric_rows = (
        [m for m in pop.all_metrics if m.file_path in targets] if pop.scoped else pop.all_metrics
    )
    data = HealthData(
        repository=repository,
        reference_repository=reference_repository,
        pop=pop,
        test_paths=test_paths,
        findings=findings,
        deductions=deductions,
        metric_rows=metric_rows,
        refactoring=_RefactoringBlocks(),
        performance=_PerformanceBlocks(),
        by_leverage=[],
        leads={},
    )
    data.hotspot_paths = await _read_hotspot_paths(session, repository, pop, req)
    data.perf_coverage, data.perf_findings_count = await _read_perf_headline(
        session, repository, pop, req, findings.lead_rows
    )
    data.accuracy_rows = await read_accuracy_rows(session, repository, pop, req)
    data.refactoring_rows, data.refactoring_recommendations = await _read_refactoring_plans(
        session, repository, pop, req
    )
    data.refactoring, data.performance = await _read_pillars(
        session, repository, reference_repository, pop, req
    )
    data.coverage_rows, data.coverage_summary = await _read_coverage(
        session, repository, pop, req
    )
    data.signals_by_path = await _read_signals(session, repository, pop, req)
    data.drift_rows, data.drift_unavailable = await _read_doc_drift(
        session, repository, pop, req
    )
    data.churn_points = await _read_churn_points(session, repository, pop, req)
    data.snapshots = await _read_snapshots(session, repository, pop, req)
    data.by_leverage, data.leads = _rank_leverage_and_leads(pop, metric_rows, findings, req)
    data.plan_biomarkers_by_path, data.plan_count_by_path = await _read_directive_plans(
        session, repository, pop, req, data.by_leverage
    )
    return data


async def _read_test_paths(
    session: Any, repository: Any, pop: Population, req: HealthRequest
) -> set[str]:
    """Test material, from the flag ingestion already decided per file.

    Gated on ``needs_test_paths`` and run after the ``module:`` expansion.
    Targeted mode reads only the named paths; dashboard mode partitions a
    finding list whose paths are not yet known, so it reads repo-wide.
    """
    if not req.needs_test_paths:
        return set()
    return await get_test_file_paths(
        session, repository.id, pop.effective_targets if pop.scoped else None
    )


async def _read_hotspot_paths(
    session: Any, repository: Any, pop: Population, req: HealthRequest
) -> set[str]:
    """Hotspot paths for the canonical hotspot-health KPI.

    Skipped in targeted mode, which builds no ``kpis`` block."""
    if pop.scoped or not req.wants("kpis"):
        return set()
    return await get_hotspot_file_paths(session, repository.id)


async def _read_perf_headline(
    session: Any, repository: Any, pop: Population, req: HealthRequest, lead_rows: list[Any]
) -> tuple[PerfCoverage | None, int]:
    """Dashboard perf headline: coverage (how much of the analyzed code the
    perf pass ran on) + open performance-finding count. Both feed ``kpis``
    alone, so a projection that drops kpis skips the language-map read."""
    if pop.scoped or not req.wants("kpis"):
        return None, 0
    lang_by_path = await get_file_language_map(session, repository.id)
    return (
        coverage_for_metrics(pop.all_metrics, lang_by_path),
        sum(1 for f in lead_rows if (f.dimension or "defect") == "performance"),
    )


async def _read_refactoring_plans(
    session: Any, repository: Any, pop: Population, req: HealthRequest
) -> tuple[list[Any], list[Recommendation]]:
    """Plans scoped to the same targets, exclude-filtered like findings."""
    if not req.plans_requested or pop.nothing_resolved:
        return [], []
    rows = pop.in_scope_rows(
        await get_refactoring_suggestions(
            session,
            repository.id,
            file_paths=list(pop.effective_targets) if pop.scoped else None,
        ),
        "file_path",
    )
    recommendations = await hydrate_recommendations(
        session,
        repository.id,
        rows,
        metric_rows=pop.all_metrics,
        view=plan_view(req.refactoring_view),
    )
    return rows, recommendations


async def _read_pillars(
    session: Any, repository: Any, reference_repository: str, pop: Population, req: HealthRequest
) -> tuple[_RefactoringBlocks, _PerformanceBlocks]:
    """The materialized causal read model. Filtering, ordering, paging, plan
    linkage, and facets are the shared service's; this tool caps the
    collection, pages it, and serializes what comes back."""
    performance_service = PerformanceHealthService(
        session, repository.id, reference_repository
    )
    refactoring_service = RefactoringHealthService(
        session, repository.id, reference_repository
    )
    refactoring = await _refactoring_blocks(
        refactoring_service,
        wants=req.wants,
        included="refactoring" in req.include_set and req.wants_refactoring_opportunities,
        file_paths=pop.target_paths,
        scoped=pop.scoped,
        limit=req.limit,
        cursor=req.cursor,
        view=req.refactoring_view,
        lead_type=req.refactoring_type,
        confidence=req.refactoring_confidence,
        effort=req.refactoring_effort,
    )
    performance = await _performance_blocks(
        performance_service,
        wants=req.wants,
        included="performance" in req.include_set and req.wants_performance_opportunities,
        file_paths=pop.target_paths,
        scoped=pop.scoped,
        limit=req.limit,
        cursor=req.cursor,
        view=req.performance_view,
        context=req.performance_context,
        boundary=req.performance_boundary,
        confidence=req.performance_confidence,
        actionability=req.performance_actionability,
        sort=req.performance_sort,
    )
    return refactoring, performance


async def _read_coverage(
    session: Any, repository: Any, pop: Population, req: HealthRequest
) -> tuple[list[Any], dict[str, Any]]:
    if "coverage" not in req.include_set or pop.nothing_resolved:
        return [], {}
    rows = pop.in_scope_rows(
        # ``effective_targets``: a raw ``module:foo`` is not a file path.
        # Only targeted mode serializes ``covered_lines``, so the dashboard
        # declines that column at the read.
        await load_coverage_for_repo(
            session,
            repository.id,
            file_paths=list(pop.effective_targets) if pop.scoped else None,
            include_covered_lines=pop.scoped,
        ),
        "file_path",
    )
    # A stored repo-wide aggregate cannot describe a narrowed population, so
    # it is omitted there; the rows stay.
    summary = (
        {}
        if pop.reported_scope == "production"
        else await get_coverage_summary(session, repository.id)
    )
    return rows, summary


async def _read_signals(
    session: Any, repository: Any, pop: Population, req: HealthRequest
) -> dict[str, dict[str, Any]]:
    """Per-file process/people/topology signals for targeted files.

    The same join the file-detail drawer and REST breakdown use."""
    if "signals" not in req.include_set or not pop.effective_targets:
        return {}
    # Batched: ``module:`` targets expand to every file in the module.
    git_meta_by_path = await get_git_metadata_bulk(
        session, repository.id, list(pop.effective_targets)
    )
    degrees_by_path = await get_node_degree_counts_bulk(
        session,
        repository.id,
        list(pop.effective_targets),
        edge_types=sorted(FILE_DEPENDENCY_EDGE_TYPES),
    )
    return {
        path: asdict(file_signals(git_meta_by_path.get(path), degrees_by_path.get(path)))
        for path in pop.effective_targets
    }


async def _read_doc_drift(
    session: Any, repository: Any, pop: Population, req: HealthRequest
) -> tuple[list[Any], str | None]:
    """Documentation this repository's own tree no longer satisfies.

    Findings are filed against the document, so ``targets`` narrows by
    document path, matched exactly."""
    if "doc_drift" not in req.include_set:
        return [], None
    drift_rows: list[Any] = []
    drift_unavailable: str | None = None
    try:
        # The savepoint matters: on an index older than the drift table this
        # raises, and on Postgres a failed statement poisons the transaction.
        async with session.begin_nested():
            rows = await get_doc_drift_findings(session, repository.id)
        # Exclude config only, not ``in_scope_rows``: the production scope
        # keeps files with a health metric, and no document has one.
        drift_rows = filter_rows_by_attr(rows, "file_path", pop.exclude_spec)
    except (SQLAlchemyError, OSError, LookupError):
        # Unavailable, not empty: an empty list would read as clean.
        drift_unavailable = UNAVAILABLE_NO_TABLE
    if pop.scoped:
        drift_rows = [r for r in drift_rows if r.file_path in pop.effective_targets]
    return drift_rows, drift_unavailable


async def _read_churn_points(
    session: Any, repository: Any, pop: Population, req: HealthRequest
) -> list[dict[str, Any]]:
    """Churn x complexity quadrant for the whole repo (dashboard mode). One
    git-metadata query joined against the already-loaded metrics."""
    if "churn_complexity" not in req.include_set or pop.scoped:
        return []
    git_meta_by_path = await get_all_git_metadata(session, repository.id)
    return [asdict(p) for p in churn_complexity_points(pop.all_metrics, git_meta_by_path)]


async def _read_snapshots(
    session: Any, repository: Any, pop: Population, req: HealthRequest
) -> list[Any]:
    """The snapshot window for the repo-level trend block and/or the
    per-file trajectory attached in targeted mode."""
    if "trend" not in req.include_set and not (pop.scoped and req.wants("trends")):
        return []
    # Same scope as the KPIs, so both describe the same files.
    return project_scope(
        await list_health_snapshots(session, repository.id, limit=20), pop.reported_scope
    )


def _rank_leverage_and_leads(
    pop: Population,
    metric_rows: list[HealthFileMetric],
    findings: FindingSets,
    req: HealthRequest,
) -> tuple[list[HealthFileMetric], dict[str, dict[str, Any]]]:
    """Dominant-cause lead per file, and the leverage ranking it is printed beside.

    Targeted mode reduces the whole (small) scoped set. Dashboard mode reduces
    only the rows of files it prints: identical output at a fraction of the cost.

    Computed inside the session because the directive's plan lookup needs
    ``by_leverage``.
    """
    if pop.scoped:
        return [], _leads_by_file(findings.lead_rows)
    # Ranked by NLOC-weighted deficit, not raw score: a big mid-band file
    # moves the average more than a tiny at-risk one.
    by_leverage = sorted(
        (m for m in pop.all_metrics if m.score < TARGET_SCORE),
        key=lambda m: max(TARGET_SCORE - m.score, 0.0) * max(m.nloc, 1),
        reverse=True,
    )
    printed = {m.file_path for m in metric_rows[: req.limit]}
    printed |= {m.file_path for m in by_leverage[: req.limit]}
    # The directive's candidates, unconditionally: its leads must not depend
    # on ``limit``, or ``limit=0`` would make it assert wrong claims.
    printed |= {m.file_path for m in by_leverage[:_DIRECTIVE_CANDIDATES]}
    return by_leverage, _leads_by_file([r for r in findings.lead_rows if r.file_path in printed])


async def _read_directive_plans(
    session: Any,
    repository: Any,
    pop: Population,
    req: HealthRequest,
    by_leverage: list[HealthFileMetric],
) -> tuple[dict[str, set[str]], dict[str, int]]:
    """Which biomarkers the stored plans for the directive's candidates actually address.

    The directive points at ``include=['refactoring']`` for the fix, but some
    biomarkers have no plan kind, so it must know whether a plan addresses the
    cause it names. Read for the directive's candidates only, two columns, and
    only when the directive survives the projection. ``status == "open"``
    mirrors ``get_refactoring_suggestions``; candidates are already
    exclude-filtered.
    """
    plan_biomarkers_by_path: dict[str, set[str]] = {}
    plan_count_by_path: dict[str, int] = {}
    if pop.scoped or not req.wants("directive") or not by_leverage:
        return plan_biomarkers_by_path, plan_count_by_path
    directive_paths = [m.file_path for m in by_leverage[:_DIRECTIVE_CANDIDATES]]
    for path, source in (
        await session.execute(
            select(
                RefactoringSuggestion.file_path,
                RefactoringSuggestion.source_biomarker,
            ).where(
                RefactoringSuggestion.repository_id == repository.id,
                RefactoringSuggestion.status == "open",
                RefactoringSuggestion.file_path.in_(directive_paths),
            )
        )
    ).all():
        # Counted apart from attribution: ``split_file`` and ``break_cycle``
        # plans store an empty ``source_biomarker``.
        plan_count_by_path[path] = plan_count_by_path.get(path, 0) + 1
        if source:
            plan_biomarkers_by_path.setdefault(path, set()).add(source)
    return plan_biomarkers_by_path, plan_count_by_path
