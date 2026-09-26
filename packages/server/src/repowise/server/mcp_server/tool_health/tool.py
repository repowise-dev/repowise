"""MCP tool: get_health — code-health markers and per-file scores."""

from __future__ import annotations

from dataclasses import asdict
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from repowise.core.analysis.doc_drift.constants import (
    DETECTION_BASIS,
    UNAVAILABLE_NO_TABLE,
)
from repowise.core.analysis.health.aggregation import module_rollups as _module_rollups
from repowise.core.analysis.health.churn_complexity import churn_complexity_points
from repowise.core.analysis.health.counts import (
    DEFAULT_COUNTS,
    parse_counts,
)
from repowise.core.analysis.health.counts import project as project_counts
from repowise.core.analysis.health.defect_accuracy import compute_defect_accuracy
from repowise.core.analysis.health.grading import TARGET_SCORE
from repowise.core.analysis.health.grading import distribution as health_distribution
from repowise.core.analysis.health.models import split_by_origin
from repowise.core.analysis.health.perf.coverage import PerfCoverage, coverage_for_metrics
from repowise.core.analysis.health.perf.opportunity_rank import NON_LEADING_MARKERS
from repowise.core.analysis.health.ranking import deduction_by_path, sort_metrics_worst_first
from repowise.core.analysis.health.refactoring.recommendations import (
    Recommendation,
    hydrate_recommendations,
)
from repowise.core.analysis.health.scope import DEFAULT_SCOPE, parse_scope
from repowise.core.analysis.health.scoring import ALL_DIMENSIONS
from repowise.core.analysis.health.signals import file_signals
from repowise.core.analysis.health.suggestions import suggestion_for
from repowise.core.analysis.health.trends import (
    diff_snapshots,
    drop_unscoped_fields,
    file_trend,
    project_scope,
    recent_kpis,
)
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
    serialize_doc_drift_row,
    summarize_confidence_rows,
)
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    HealthFileMetric,
    HealthFinding,
    RefactoringSuggestion,
)
from repowise.core.registry import ToolRecipe
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    filter_rows_by_attr,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server.tool_health.analysis_meta import (
    _attach_health_analysis_meta,
    _attach_repository_analysis_meta,
)
from repowise.server.mcp_server.tool_health.coverage import (
    _attach_coverage_decay,
    _serialize_coverage_row,
)
from repowise.server.mcp_server.tool_health.details import (
    _note_inapplicable_controls,
    _performance_detail_response,
    _refactoring_detail_response,
    _resolve_finding,
    _selector_conflict,
)
from repowise.server.mcp_server.tool_health.paging import (
    _stamp_collection,
    _stamp_nested_collections,
)
from repowise.server.mcp_server.tool_health.pillars import (
    _PERFORMANCE_COLLECTION_CAP,
    _REFACTORING_COLLECTION_CAP,
    _REFACTORING_OPPORTUNITY_PREFIX,
    _performance_blocks,
    _refactoring_blocks,
)
from repowise.server.mcp_server.tool_health.plans import (
    _refactoring_plans_status,
    _validation_profile,
)
from repowise.server.mcp_server.tool_health.serialize import (
    _rank_emitted,
    _serialize_finding,
    _serialize_metric,
    _serialize_refactoring,
)
from repowise.server.mcp_server.tool_health.summary import (
    _DIRECTIVE_CANDIDATES,
    _compute_kpis,
    _directive,
    _gap_analysis,
    _leads_by_file,
)
from repowise.server.mcp_server.tool_health.targets import _unresolved_targets
from repowise.server.services.performance_health import PerformanceHealthService
from repowise.server.services.refactoring_health import CANONICAL_VIEWS as _REFACTORING_VIEWS
from repowise.server.services.refactoring_health import DEFAULT_VIEW as _REFACTORING_VIEW_DEFAULT
from repowise.server.services.refactoring_health import (
    RefactoringHealthService,
    plan_view,
)

# ``include`` and ``only`` were different vocabularies: the block a caller
# switches on with ``include=["biomarkers"]`` lands under the key ``findings``,
# so the obvious ``only=["biomarkers"]`` projected it away again. Alias the three
# that have a 1:1 key rather than make the caller learn two names for one block.
# ``signals`` is deliberately absent — it has no top-level key to alias to, it
# merges into ``metrics[].signals``, so it stays reported in ``unknown_only_keys``.
_ONLY_ALIASES = {
    "biomarkers": "findings",
    "accuracy": "defect_accuracy",
    "refactoring": "refactoring_plans",
}


_RANKED_DIMENSIONS_DEFAULT = {"defect", "maintainability"}
"""Dimensions the impact-ranked findings list carries when none is asked for."""

def _in_dimensions(row: Any, dimensions: set[str]) -> bool:
    """True when *row* belongs to one of *dimensions* (empty set -> everything).

    ``dimension`` is nullable and a NULL means ``defect``: the column was added
    without a backfill, so pre-existing rows stay NULL until the next index
    recomputes them. Reading it as anything else drops real defect findings.
    """
    if not dimensions:
        return True
    return (row.dimension or "defect") in dimensions


@mcp.tool(
    surface_order=90,
    artifact_type="health",
    presentation="health",
    evidence_basis="measured",
    recipes=(
        ToolRecipe(
            "health_directive",
            'get_health(only=["directive"])',
            ("get_health",),
        ),
        ToolRecipe(
            "health_file_self_check",
            'get_health(targets=["path"], include=["refactoring"])',
            ("get_health",),
        ),
        ToolRecipe(
            "health_module_triage",
            'get_health(targets=["module:path"], only=["modules","metrics"])',
            ("get_health",),
        ),
        ToolRecipe(
            "health_trend",
            'get_health(include=["trend"], only=["trend"])',
            ("get_health",),
        ),
        ToolRecipe(
            "health_accuracy",
            'get_health(include=["accuracy"], only=["accuracy"])',
            ("get_health",),
        ),
        ToolRecipe(
            "health_coverage",
            'get_health(include=["coverage"], only=["coverage"])',
            ("get_health",),
        ),
        ToolRecipe(
            "health_performance_refactoring",
            'get_health(include=["performance","refactoring"], '
            'only=["performance_opportunities","refactoring_plans"])',
            ("get_health",),
        ),
        ToolRecipe(
            "health_performance_summary",
            'get_health(include=["performance"], only=["performance_summary"])',
            ("get_health",),
        ),
        ToolRecipe(
            "health_performance_opportunity",
            'get_health(opportunity_id="perf...")',
            ("get_health",),
        ),
        ToolRecipe(
            "health_performance_evidence",
            'get_health(opportunity_id="perf...", '
            'only=["performance_evidence"], cursor=0)',
            ("get_health",),
        ),
    ),
)
async def get_health(
    targets: list[str] | None = None,
    include: list[str] | None = None,
    repo: str | None = None,
    limit: int = 20,
    only: list[str] | None = None,
    refactoring_view: str = _REFACTORING_VIEW_DEFAULT,
    refactoring_type: str | None = None,
    refactoring_confidence: str | None = None,
    refactoring_effort: str | None = None,
    cursor: int = 0,
    finding_id: str | None = None,
    plan_id: str | None = None,
    opportunity_id: str | None = None,
    performance_view: str | None = None,
    performance_context: str | None = None,
    performance_boundary: str | None = None,
    performance_confidence: str | None = None,
    performance_actionability: str | None = None,
    performance_sort: str | None = None,
    scope: str = DEFAULT_SCOPE,
    counts: str = DEFAULT_COUNTS,
) -> dict:
    """Code-health scores and findings from stored analysis.

    No ``targets`` returns a dashboard; targets rank files and findings.
    Never recomputes health: commit, then run ``repowise update``.
    Every block and accepted value: docs/agent/MCP_TOOLS.md.

    Args:
        targets: file paths or ``module:<name>``; unmatched ones land in
            ``unresolved``.
        include: ``biomarkers``|``refactoring``|``trend``|``coverage``|
            ``accuracy``|``signals``|``churn_complexity``|``doc_drift``,
            or a dimension incl. ``advisory``; ``performance`` and
            ``refactoring`` add queues.
        only: keys to keep; identity, totals, recovery survive.
            ``biomarkers``/``accuracy``/``refactoring`` alias their block key;
            ``performance``/``defect``/``maintainability``/``advisory``
            do not: they filter rows into ``unknown_only_keys``.
        repo: usually omitted.
        limit: max rows per ranked list, ``0`` for none.
        cursor: zero-based offset into a ranked list.
        finding_id/plan_id: stable ``id`` from a finding or plan.
        opportunity_id: ``perf...``/``refop...``: the unit, its steps or
            plan, evidence paged by ``only=["*_evidence"]``.
        refactoring_view: ``diversified`` (default)|``canonical``|
            ``file_spread``; _type/_confidence/_effort filter.
        performance_view/_context/_boundary/_confidence/_actionability/_sort:
            queue filters; a rejected value lists the accepted.
        scope / counts: default ``all``/``everything``. ``production`` drops
            test files; ``code_shape`` drops the git-derived half of the
            score and its findings.

    """
    started = perf_counter()
    conflict = _selector_conflict(
        finding_id=finding_id, plan_id=plan_id, opportunity_id=opportunity_id
    )
    if conflict is not None:
        return _note_inapplicable_controls(conflict, scope, counts)
    # ``0`` means none, matching the ``module_limit`` convention on the REST
    # coverage route. It used to clamp up to 1, so the documented way to ask for
    # "the totals, none of the rows" silently returned a row.
    limit = max(limit, 0)
    cursor = max(cursor, 0)
    if refactoring_view not in _REFACTORING_VIEWS:
        refactoring_view = _REFACTORING_VIEW_DEFAULT
    include_set = set(include or [])
    known_includes = {
        "biomarkers",
        "refactoring",
        "trend",
        "coverage",
        "accuracy",
        "signals",
        "churn_complexity",
        "doc_drift",
        "performance",
        "defect",
        "maintainability",
        "advisory",
    }
    unknown_include_keys = sorted(include_set - known_includes)
    only_list = [_ONLY_ALIASES.get(k, k) for k in (only or [])]
    only_set = set(only_list)

    def wants(block: str) -> bool:
        """True when ``block`` survives the ``only`` projection.

        ``only`` used to be applied to the finished response, so the cheapest
        documented call — ``only=["directive"]`` — still paid for every block it
        then discarded. Consulted before the expensive optional work so the
        projection gates the work as well as the payload.
        """
        return not only_set or block in only_set

    # Resolved before the reads, not after them. Applied to the finished
    # response, a dimension filter narrowed a list that had already been capped
    # by impact — and performance findings carry low impact by construction, so
    # ``include=["biomarkers", "performance"]`` filtered a defect-heavy head down
    # to nothing while the total still reported the whole repo. The filter now
    # decides which rows are eligible for the cap in the first place.
    dimension_filter = include_set & set(ALL_DIMENSIONS)
    # The ranked findings list is ordered by health impact, and every
    # performance finding carries zero impact by construction, so leaving it
    # in an unfiltered list appends rows that can never rank and cannot be
    # compared against the ones above them. Asking for the dimension still
    # returns it, and the performance blocks rank the same evidence by cause.
    ranked_dimensions = dimension_filter or _RANKED_DIMENSIONS_DEFAULT
    # The serialized-rows read is the expensive optional one; skip it when no
    # block that carries findings survives the projection.
    wants_findings = wants("findings") or wants("top_findings")
    wants_test_findings = wants("test_findings")
    wants_performance_opportunities = (
        wants("performance_opportunities")
        or wants("recommendation_lede")
        or wants("performance_summary")
    )
    wants_refactoring_opportunities = (
        wants("refactoring_opportunities")
        or wants("recommendation_lede")
        or wants("refactoring_summary")
    )
    # Everything downstream of the test/production split, in one place.
    #
    # Keep this list exhaustive. The read it gates is not free (the column list
    # is narrow but the predicate is not indexed, so it scans this repo's graph
    # nodes — ~55 ms warm on a 35k-node index), and ``only=["directive"]`` /
    # ``["kpis"]`` / ``["modules"]`` serialize no metric row and no finding.
    # But a *missing* entry here is worse than the read: it makes the split
    # collapse for that projection, which is the same "a projection changed
    # what a surviving key holds" defect this change exists to close. Adding
    # ``suggestion_legend`` was not optional — the legend derives from the split
    # heads, and leaving it out silently reverted that fix.
    needs_test_paths = (
        wants_findings
        or wants_test_findings
        or wants("worst_files")
        or wants("high_leverage_files")
        or wants("metrics")
        or ("refactoring" in include_set and wants("suggestion_legend"))
    )

    # Split ``module:foo`` targets out of the path list. A target that
    # matches one or more modules is expanded into the set of files
    # belonging to those modules.
    raw_targets = list(targets or [])
    module_targets = [t.split(":", 1)[1] for t in raw_targets if t.startswith("module:")]
    # Stored paths are POSIX-separated. Normalize so a Windows caller passing
    # ``packages\core\x.py`` matches instead of coming back ``no_such_path``.
    file_targets = [t.replace("\\", "/") for t in raw_targets if not t.startswith("module:")]

    ctx = await _resolve_repo_context(repo)
    omission_collector = OmissionCollector("get_health", repo_root=ctx.path)
    semantic_omissions: dict[str, list[Any]] = {}
    page_recoveries: dict[str, tuple[int, int, int]] = {}
    paged_collections = {
        "metrics",
        "findings",
        "trends",
        "worst_files",
        "high_leverage_files",
        "top_findings",
        "test_findings",
        "modules",
        "churn_complexity",
        "coverage.files",
        "doc_drift.findings",
        "refactoring_plans",
        "refactoring_opportunities",
        "refactoring_evidence",
        "performance_opportunities",
        "performance_evidence",
    }

    def bounded(rows: list[Any], label: str, *, cap: int | None = None) -> list[Any]:
        """Bound one collection and retain its exact tail in shared omission storage."""
        row_cap = limit if cap is None else cap
        start = cursor if label in paged_collections else 0
        kept = rows[start : start + row_cap]
        if len(kept) < len(rows):
            tail_start = start + len(kept)
            if label in paged_collections:
                if tail_start < len(rows):
                    next_limit = (
                        min(row_cap or 6, 6)
                        if label
                        in {
                            "refactoring_plans",
                            "refactoring_opportunities",
                            "performance_opportunities",
                        }
                        else min(len(rows) - tail_start, 50)
                    )
                    page_recoveries[label] = (
                        tail_start,
                        next_limit,
                        len(rows) - tail_start,
                    )
                elif rows and start >= len(rows):
                    page_recoveries[label] = (0, min(len(rows), max(row_cap, 1), 50), len(rows))
            else:
                semantic_omissions[label] = rows[row_cap:]
        return kept
    # Performance headline inputs (dashboard mode): filled inside the session.
    perf_coverage: PerfCoverage | None = None
    perf_findings_count = 0
    # ``None`` means "not read", which is what keeps the code/non-code KPI split
    # off targeted responses rather than reporting it over one file.
    lang_by_path: dict[str, str] | None = None
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        reference_repository = ctx.alias or repository.name

        if finding_id:
            match = await _resolve_finding(
                session, repository.id, finding_id, reference_repository
            )
            result = {
                "mode": "finding",
                "finding_id": finding_id,
                "finding": (
                    _serialize_finding(match, reference_repository) if match else None
                ),
                "resolved": match is not None,
                "_meta": _build_meta(
                    repository=repository,
                    targets=[match.file_path] if match else None,
                ),
            }
            await _attach_repository_analysis_meta(session, repository, result["_meta"])
            return _note_inapplicable_controls(result, scope, counts)

        if opportunity_id and opportunity_id.startswith(_REFACTORING_OPPORTUNITY_PREFIX):
            return _note_inapplicable_controls(
                await _refactoring_detail_response(
                    session,
                    repository,
                    reference_repository,
                    opportunity_id,
                    evidence_only=only_set == {"refactoring_evidence"},
                    limit=limit,
                    cursor=cursor,
                ),
                scope,
                counts,
            )

        if opportunity_id:
            return _note_inapplicable_controls(
                await _performance_detail_response(
                    session,
                    repository,
                    reference_repository,
                    opportunity_id,
                    evidence_only=only_set == {"performance_evidence"},
                    limit=limit,
                    cursor=cursor,
                ),
                scope,
                counts,
            )

        if plan_id:
            # An indexed seek and one hydration, not a full load and a linear
            # scan: resolving one id used to cost every open plan in the repo.
            service = RefactoringHealthService(
                session, repository.id, reference_repository
            )
            resolved = await service.plan_detail(plan_id)
            plan = resolved.get("plan") if resolved.get("resolved") else None
            if plan is not None:
                plan.setdefault("id", plan_id)
                plan["repository"] = reference_repository
            result = {
                "mode": "refactoring_plan",
                "plan_id": plan_id,
                "plan": plan,
                "resolved": bool(resolved.get("resolved")),
                "_meta": _build_meta(
                    repository=repository,
                    targets=[plan["file_path"]] if plan else None,
                ),
            }
            if resolved.get("opportunity_id"):
                result["opportunity_id"] = resolved["opportunity_id"]
                result["next_action"] = resolved["next_action"]
            elif plan is not None:
                result["opportunity_note"] = (
                    "This plan is addressable but is not a step of any composed "
                    "opportunity; a demoted clone is supporting evidence, not work."
                )
            await _attach_repository_analysis_meta(session, repository, result["_meta"])
            return _note_inapplicable_controls(result, scope, counts)

        all_metrics_q = select(HealthFileMetric).where(
            HealthFileMetric.repository_id == repository.id
        )
        exclude_spec = _get_exclude_spec(ctx.path)
        indexed_rows = list((await session.execute(all_metrics_q)).scalars().all())
        all_metrics = filter_rows_by_attr(indexed_rows, "file_path", exclude_spec)
        # Paths the index knows about but the exclude config drops. Kept so an
        # unresolved target can report "excluded" (a config decision) rather
        # than "no_such_path" (a typo) — the two need different responses.
        # Computed before ``scope`` narrows the list, or a test file would be
        # reported as dropped by a config that says nothing about it.
        excluded_paths = {m.file_path for m in indexed_rows} - {m.file_path for m in all_metrics}

        # Narrowing to production is the same shape of question as the exclude
        # config: both drop whole files from every block at once. Folding it
        # into one filter is what keeps a scoped dashboard from ranking a
        # finding on a file its own file list no longer contains.
        reported_scope = parse_scope(scope)
        scope_paths: set[str] | None = None
        if reported_scope == "production":
            all_metrics = [m for m in all_metrics if not m.is_test]
            scope_paths = {m.file_path for m in all_metrics}

        # Composes with the scope: a re-read off the stored structure/history
        # split, not a rescore. Findings narrow by origin, not by path, so a row
        # it cannot read lands in ``unscored_files`` with its findings intact.
        reported_counts = parse_counts(counts)
        code_shape = reported_counts == "code_shape"
        unscored_files = 0
        unscored_paths: set[str] = set()
        if code_shape:
            before = {m.file_path for m in all_metrics}
            all_metrics, unscored_files = project_counts(counts, all_metrics)
            unscored_paths = before - {m.file_path for m in all_metrics}

        def in_scope_rows(rows: list, attr: str = "file_path") -> list:
            rows = filter_rows_by_attr(rows, attr, exclude_spec)
            if scope_paths is None:
                return rows
            return [r for r in rows if getattr(r, attr, None) in scope_paths]

        def in_counts_findings(rows: list) -> list:
            """A history finding cannot explain a score its half was taken out
            of, so it is not part of the code-shape reading."""
            return split_by_origin(rows)[0] if code_shape else rows
        matched_modules: set[str] = set()
        if module_targets:
            module_set = set(module_targets)
            for m in all_metrics:
                if m.module in module_set:
                    matched_modules.add(m.module)
                    file_targets.append(m.file_path)
            file_targets = sorted(set(file_targets))

        # A non-empty ``targets`` means the caller asked for a scope, and that
        # holds even when nothing resolves. Keying the mode off the *resolved*
        # paths let ``targets=["module:typo"]`` fall through to dashboard mode
        # and answer a module-scoped question with repo-wide numbers — an
        # answer that reads as scoped and is not.
        scoped = bool(raw_targets)
        effective_targets = file_targets if scoped else []
        nothing_resolved = scoped and not effective_targets

        # Test material, from the flag ingestion already decided per file.
        # Gated on ``needs_test_paths`` — see the note at its definition — and
        # placed after the ``module:`` expansion so targeted mode can scope it.
        #
        # Targeted mode only ever asks ``path in test_paths`` for paths the
        # caller named, so it reads exactly those; dashboard mode partitions a
        # ranked finding list whose paths are not known until the read below
        # runs, so it keeps the repo-wide answer. Measured on this repo, that
        # is 32.9ms -> 0.6ms on a single-file target — a quarter of the whole
        # call, paid to answer "is this one file a test".
        test_paths: set[str] = set()
        if needs_test_paths:
            test_paths = await get_test_file_paths(
                session, repository.id, effective_targets if scoped else None
            )

        open_findings = (
            HealthFinding.repository_id == repository.id,
            HealthFinding.status == "open",
        )
        # Two row sets, deliberately split.
        #
        # ``finding_rows`` are the ones this response will serialize.
        # ``lead_rows`` is the wider set the per-file dominant-cause reduction
        # and the exact totals are computed from — it only ever needs four
        # columns.
        #
        # Targeted mode asks about a handful of files, so one full read serves
        # both. Dashboard mode does not: hydrating every open finding as a full
        # ORM object to emit ``limit`` of them measured 262ms on this repo, and
        # that cost is linear in finding count, so it grows with the repo the
        # dashboard is describing.
        #
        # ``test_finding_rows`` is the dashboard-only test bucket (see the split
        # below). Targeted mode never fills it: the caller named the files, so
        # partitioning what they explicitly asked about would be answering a
        # different question than the one they asked.
        test_finding_rows: list[Any] = []
        test_findings_total = 0
        if scoped:
            finding_rows = in_counts_findings(in_scope_rows(
                list(
                    (
                        await session.execute(
                            select(HealthFinding)
                            .where(*open_findings)
                            .where(HealthFinding.file_path.in_(effective_targets))
                            .order_by(HealthFinding.health_impact.desc())
                        )
                    )
                    .scalars()
                    .all()
                ),
                "file_path",
            ))
            lead_rows: list[Any] = finding_rows
            emitted = _rank_emitted(
                [f for f in finding_rows if _in_dimensions(f, ranked_dimensions)]
            )
            finding_rows = emitted
            legend_rows: list[Any] = finding_rows
        else:
            # Narrow read over every open finding: the four attributes
            # ``_leads_by_file`` reads, plus ``dimension`` for the perf headline
            # and ``id`` to fetch the head. SQLAlchemy ``Row`` exposes these as
            # attributes, so the reduction and the exclude filter both run
            # against it unchanged.
            #
            # ``details_json`` joins them only when the caller asked for the
            # performance dimension, because that is the only case where a perf
            # finding can reach the head at all: every one carries
            # ``health_impact: 0``, so in a mixed list all ~10k defect findings
            # sort above them and the rank could not move a row. Measured on
            # this repo the column costs 6.6ms on the read (parsing the 697 perf
            # rows out of 10,740 costs a further 1.2ms), which is worth paying
            # for the one call it changes and not worth paying for the default.
            lite_cols = [
                HealthFinding.id,
                HealthFinding.file_path,
                HealthFinding.health_impact,
                HealthFinding.biomarker_type,
                HealthFinding.reason,
                HealthFinding.dimension,
            ]
            if "performance" in dimension_filter:
                lite_cols.append(HealthFinding.details_json)
            if wants_performance_opportunities:
                lite_cols.extend(
                    [
                        HealthFinding.function_name,
                        HealthFinding.line_start,
                        HealthFinding.line_end,
                    ]
                )
            lite_rows = list(
                (
                    await session.execute(
                        select(*lite_cols)
                        .where(*open_findings)
                        .order_by(HealthFinding.health_impact.desc())
                    )
                ).all()
            )
            # ``lead_rows`` stays the unfiltered open set: it feeds the per-file
            # leads and the performance KPI, neither of which should change
            # because the caller asked to *see* one dimension.
            lead_rows = in_counts_findings(in_scope_rows(lite_rows))
            emitted = _rank_emitted(
                [r for r in lead_rows if _in_dimensions(r, ranked_dimensions)]
            )
            # Test material goes in its own bucket rather than competing for
            # the repo's headline finding list. Measured on this repo, **2 of
            # the top 5** open findings by impact sit on test files, and 4-5 of
            # the top 20 — the top-20 figure is tie-dependent (ranks 14+ are all
            # at impact 2.16), which is itself the point: a fifth of the
            # most-read list was the test suite, decided partly by tie-break.
            # Splitting keeps both readable. A thrashing test suite is a real
            # signal some teams want; it is just not the same question as
            # "where is the defect risk in this codebase".
            #
            # Split *before* the cap, so each list is the top ``limit`` of its
            # own population — capping first and partitioning after would give
            # the smaller bucket whatever happened to land in the head.
            prod_emitted = [r for r in emitted if r.file_path not in test_paths]
            test_emitted = [r for r in emitted if r.file_path in test_paths]
            # Both heads, in one list, decided here rather than downstream: the
            # legend has to be a pure function of the ranked set so no
            # projection can change what a surviving key contains.
            legend_rows: list[Any] = prod_emitted[:limit] + test_emitted[:limit]
            # Fetch the head by id rather than re-running the ranked query with
            # an over-fetch margin. The margin had to cover every exclusion in
            # the table, so a repo excluding a large subtree turned the "capped"
            # read back into a near-full one; by id it is exactly ``limit`` rows
            # whatever the exclude config or dimension filter say.
            head_ids = [r.id for r in prod_emitted[:limit]]
            test_head_ids = [r.id for r in test_emitted[:limit]] if wants_test_findings else []
            finding_rows = []
            if not wants_findings:
                head_ids = []
            # One read for both heads — the split is a partition of the same
            # ranked set, so paying two round-trips for it would be the N+1 this
            # tool flags in itself.
            wanted_ids = head_ids + test_head_ids
            if wanted_ids:
                by_id = {
                    f.id: f
                    for f in (
                        await session.execute(
                            select(HealthFinding).where(HealthFinding.id.in_(wanted_ids))
                        )
                    )
                    .scalars()
                    .all()
                }
                # Re-imposed from the id lists; ``IN`` does not preserve order.
                finding_rows = [by_id[i] for i in head_ids if i in by_id]
                test_finding_rows = [by_id[i] for i in test_head_ids if i in by_id]
            test_findings_total = len(test_emitted)

        action_finding_rows = emitted
        # Counts the rows this response is about: the post-exclusion open set,
        # narrowed to the requested dimensions when one was asked for. Reporting
        # the unfiltered total beside a filtered list is what made an empty
        # ``findings`` read as "nothing here" rather than "nothing shown".
        #
        # In dashboard mode this counts the *production* half, because that is
        # the list it sits beside; ``test_findings_total`` counts the other half
        # and the two still sum to the whole open set. Same rule #1337 settled
        # for the dimension filter: a total describes the list it is a sibling
        # of, never a wider set the caller cannot see.
        findings_total = len(emitted if scoped else prod_emitted)

        # Worst-first order, placed here because ranking needs the summed
        # deduction per file and ``lead_rows`` is the first point that carries
        # every open finding this response is entitled to see. Same comparator
        # the crud layer applies to ``get_health_metrics``, so the REST
        # dashboard and this tool cannot disagree about which file is worst —
        # but fed from rows already in memory, so it costs no extra query.
        #
        # Deliberately ``lead_rows`` (the unfiltered open set) rather than
        # ``emitted``: asking to *see* one dimension must not restate which
        # files the repo's worst are.
        deductions = deduction_by_path(lead_rows)
        # Rebound rather than kept beside a sorted copy, and above every reader.
        # ``kpis``, the leverage view and the churn quadrant all reduce with
        # ``min()`` or a stable sort, which resolve ties by *input* order — so
        # leaving them on the raw list would have one response name one file as
        # the worst performer while the ``worst_files`` list printed below it
        # led with another. The module rollup takes the map itself.
        all_metrics = sort_metrics_worst_first(all_metrics, deductions)
        metric_rows = (
            [m for m in all_metrics if m.file_path in set(effective_targets)]
            if scoped
            else all_metrics
        )

        # Hotspot health was the one repo KPI this tool never returned, while
        # ``get_overview`` invented its own definition for it — so the canonical
        # persisted number was surfaced by neither. One scalar column, gated the
        # same way as the language map below: ``targets`` mode builds no ``kpis``
        # block at all, so scoping the call must not pay for this read.
        hotspot_paths: set[str] = set()
        if not scoped and wants("kpis"):
            hotspot_paths = await get_hotspot_file_paths(session, repository.id)

        # Dashboard perf headline: coverage (how much of the analyzed code the
        # perf pass ran on) + open performance-finding count. Both feed ``kpis``
        # alone, so a projection that drops kpis skips the language-map read.
        if not scoped and wants("kpis"):
            lang_by_path = await get_file_language_map(session, repository.id)
            perf_coverage = coverage_for_metrics(all_metrics, lang_by_path)
            perf_findings_count = sum(
                1 for f in lead_rows if (f.dimension or "defect") == "performance"
            )

        # ``accuracy`` scores the ranking against the whole repo rather than the
        # capped head, but it reads exactly one biomarker: ``compute_defect_accuracy``
        # ignores every finding whose type is not ``prior_defect``. Selecting
        # those directly keeps the honest denominator without re-reading the
        # ~10k rows the narrow pass above exists to avoid.
        # Not routed through ``in_counts_findings``: ``prior_defect`` is the
        # ground truth this block scores the number against, not a deduction
        # the reading includes. Dropping it under ``code_shape`` would leave
        # the accuracy block with no labels to be accurate about.
        accuracy_rows: list[Any] = []
        if "accuracy" in include_set and not scoped:
            accuracy_rows = in_scope_rows(
                list(
                    (
                        await session.execute(
                            select(HealthFinding)
                            .where(*open_findings)
                            .where(HealthFinding.biomarker_type == "prior_defect")
                        )
                    )
                    .scalars()
                    .all()
                ),
                "file_path",
            )

        # Structured refactoring plans (Extract Class, ...) — loaded only when
        # asked for, scoped to the same targets, exclude-filtered like findings.
        refactoring_rows: list[Any] = []
        refactoring_recommendations: list[Recommendation] = []
        # Only when a caller names the plan list. ``include=["refactoring"]``
        # leads with composed opportunities now, and emitting both would ship
        # two representations of the same work in one response - 52k chars on
        # this repo, past the expanded budget, most of it duplicated. The
        # documented ``only=["refactoring_plans"]`` call is unchanged.
        plans_requested = "refactoring" in include_set and (
            "refactoring_plans" in only_set
            # The cross-pillar lede quotes one plan beside one performance
            # opportunity, and only when both pillars were asked for.
            or ({"performance", "refactoring"} <= include_set and wants("recommendation_lede"))
        )
        if plans_requested and not nothing_resolved:
            refactoring_rows = in_scope_rows(
                await get_refactoring_suggestions(
                    session,
                    repository.id,
                    file_paths=list(effective_targets) if scoped else None,
                ),
                "file_path",
            )
            refactoring_recommendations = await hydrate_recommendations(
                session,
                repository.id,
                refactoring_rows,
                metric_rows=all_metrics,
                view=plan_view(refactoring_view),
            )

        # The materialized causal read model. Filtering, ordering, paging, plan
        # linkage, and facets are the shared service's; this tool caps the
        # collection, pages it, and serializes what comes back.
        performance_service = PerformanceHealthService(
            session, repository.id, reference_repository
        )
        refactoring_service = RefactoringHealthService(
            session, repository.id, reference_repository
        )
        refactoring = await _refactoring_blocks(
            refactoring_service,
            wants=wants,
            included="refactoring" in include_set and wants_refactoring_opportunities,
            file_paths=tuple(effective_targets) if scoped else None,
            scoped=scoped,
            limit=limit,
            cursor=cursor,
            view=refactoring_view,
            lead_type=refactoring_type,
            confidence=refactoring_confidence,
            effort=refactoring_effort,
        )
        performance = await _performance_blocks(
            performance_service,
            wants=wants,
            included="performance" in include_set and wants_performance_opportunities,
            file_paths=tuple(effective_targets) if scoped else None,
            scoped=scoped,
            limit=limit,
            cursor=cursor,
            view=performance_view,
            context=performance_context,
            boundary=performance_boundary,
            confidence=performance_confidence,
            actionability=performance_actionability,
            sort=performance_sort,
        )

        coverage_rows: list[Any] = []
        coverage_summary: dict[str, Any] = {}
        if "coverage" in include_set and not nothing_resolved:
            coverage_rows = in_scope_rows(
                # ``effective_targets``, not ``targets`` — a raw ``module:foo``
                # target is not a file path and matched nothing here.
                #
                # Only targeted mode serializes ``covered_lines``. The dashboard
                # used to read every ``covered_lines_json`` blob, ``json.loads``
                # each one, and then strip the field back out with a dict
                # comprehension — 466,874 B of parse per call for a key it never
                # emitted. Decline the column at the read instead.
                await load_coverage_for_repo(
                    session,
                    repository.id,
                    file_paths=list(effective_targets) if scoped else None,
                    include_covered_lines=scoped,
                ),
                "file_path",
            )
            # A repo-wide stored aggregate, not recomputed here, so it cannot
            # describe a narrowed population. Omitted rather than served beside
            # per-file rows that no longer match it; the rows themselves stay.
            coverage_summary = (
                {}
                if reported_scope == "production"
                else await get_coverage_summary(session, repository.id)
            )

        # Per-file process/people/topology signals for targeted files — the
        # same join the file-detail drawer and REST breakdown use, so an agent
        # can read why a file is risky (prior defects, churn, owners, degree)
        # before touching it. Targeted mode only; the target set is small.
        signals_by_path: dict[str, dict[str, Any]] = {}
        if "signals" in include_set and effective_targets:
            # Batched, not per-file. This loop used to issue three round-trips
            # per target (git metadata, graph node, degree counts) — the exact
            # cross-function N+1 the tool's own ``io_in_loop`` biomarker flags
            # here. ``module:`` targets expand to every file in the module, so
            # the target set is not always small.
            git_meta_by_path = await get_git_metadata_bulk(
                session, repository.id, list(effective_targets)
            )
            degrees_by_path = await get_node_degree_counts_bulk(
                session,
                repository.id,
                list(effective_targets),
                edge_types=sorted(FILE_DEPENDENCY_EDGE_TYPES),
            )
            for path in effective_targets:
                signals_by_path[path] = asdict(
                    file_signals(git_meta_by_path.get(path), degrees_by_path.get(path))
                )

        # Documentation this repository's own tree no longer satisfies. A
        # finding is filed against the DOCUMENT, so ``targets`` narrows by the
        # document path: naming ``docs/a.md`` asks about drift in that file.
        # Targets are matched exactly, as everywhere else in this tool, so a
        # bare directory resolves to nothing and lands in ``unresolved``.
        drift_rows: list[Any] = []
        drift_unavailable: str | None = None
        if "doc_drift" in include_set:
            try:
                # The savepoint is not decoration. This read raises on an index
                # written before the drift table existed, and on Postgres a
                # failed statement poisons the transaction, so without it one
                # missing table would take every later read in this call down
                # with it. ``replace_doc_drift_guarded`` guards the
                # write side against the same hazard.
                async with session.begin_nested():
                    rows = await get_doc_drift_findings(session, repository.id)
                # Only the exclude config, NOT ``in_scope_rows``. That helper
                # also applies the ``production`` scope, whose path set is the
                # files carrying a health metric --- and no markdown file
                # carries one. Routing drift through it made
                # ``scope="production"`` report every document as clean, which
                # is the one answer this detector must never give by accident.
                drift_rows = filter_rows_by_attr(rows, "file_path", exclude_spec)
            except (SQLAlchemyError, OSError, LookupError):
                # Say the block could not be read rather than serve an empty
                # list, which would read as a clean bill of health that was
                # never taken.
                drift_unavailable = UNAVAILABLE_NO_TABLE
            if scoped:
                drift_rows = [r for r in drift_rows if r.file_path in effective_targets]

        # Churn x complexity quadrant for the whole repo (dashboard mode). One
        # git-metadata query joined against the already-loaded metrics.
        churn_points: list[dict[str, Any]] = []
        if "churn_complexity" in include_set and not scoped:
            git_meta_by_path = await get_all_git_metadata(session, repository.id)
            churn_points = [asdict(p) for p in churn_complexity_points(all_metrics, git_meta_by_path)]

        # Load the snapshot window for the repo-level trend block and/or the
        # per-file trajectory we attach in targeted mode ("should I touch this
        # file" context for agents).
        snapshots: list[Any] = []
        if "trend" in include_set or (scoped and wants("trends")):
            # Read through the same scope as the KPIs, or the two halves of one
            # response would disagree about which files they describe.
            snapshots = project_scope(
                await list_health_snapshots(session, repository.id, limit=20), reported_scope
            )

        # Dominant-cause lead per file. Targeted mode wants one per target, so
        # the reduction runs over the whole (small) scoped set. Dashboard mode
        # only ever prints a lead for the files it emits, so it reduces just
        # those rows instead of all ~10k — identical output, and
        # ``_leads_by_file`` measured ~148ms per call handed the full set.
        #
        # Computed inside the session because the directive's plan lookup below
        # needs ``by_leverage`` and has to run before the session closes.
        if scoped:
            by_leverage: list[HealthFileMetric] = []
            lead_source: list[Any] = lead_rows
        else:
            # Leverage view: files ranked by NLOC-weighted deficit (how much
            # each drags the headline), not by raw score. Distinct from
            # worst_files — a big mid-band file outranks a tiny at-risk
            # one here because fixing it moves the average far more. Computed
            # before the leads so the set of printed files is known.
            by_leverage = sorted(
                (m for m in all_metrics if m.score < TARGET_SCORE),
                key=lambda m: max(TARGET_SCORE - m.score, 0.0) * max(m.nloc, 1),
                reverse=True,
            )
            printed = {m.file_path for m in metric_rows[:limit]}
            printed |= {m.file_path for m in by_leverage[:limit]}
            # The directive's three candidates, unconditionally — it reads
            # ``by_leverage[:3]`` and is not a ranked list, so its leads must not
            # depend on ``limit``. Before ``limit=0`` existed this was covered by
            # the clamp to 1 only by accident; at 0 the lead set came back empty
            # and the directive degraded to a fallback ``reason`` ("scores 1.0")
            # *and* asserted ``plan_addresses_reason: false`` on every file —
            # a wrong claim rather than a missing one.
            printed |= {m.file_path for m in by_leverage[:_DIRECTIVE_CANDIDATES]}
            lead_source = [r for r in lead_rows if r.file_path in printed]
        leads = _leads_by_file(lead_source)

        # Which biomarkers the stored plans for the directive's candidates
        # actually address. The directive names a file and a ``reason``, then
        # points at ``include=['refactoring']`` for the fix — but no detector
        # emits a plan for ``coverage_gradient``, which is the dominant cause on
        # most of this repo's worst files, so that promise was unkeepable and
        # silent about it. Read for the three named files only (``fix_first``
        # plus the two in ``then``), and only when the directive survives the
        # projection, so ``only=["directive"]`` stays the cheapest useful call.
        # Two columns, not whole rows: this reads one field, and the ORM row
        # carries ``plan_json`` + ``evidence_json`` + ``blast_radius_json``.
        # ``status == "open"`` mirrors ``get_refactoring_suggestions`` so the
        # directive cannot claim a plan the ``refactoring`` block would not
        # return. Candidate paths come from ``by_leverage`` ⊆ ``all_metrics``,
        # already exclude-filtered, so the ``IN`` needs no second pass through
        # the exclude spec.
        plan_biomarkers_by_path: dict[str, set[str]] = {}
        plan_count_by_path: dict[str, int] = {}
        if not scoped and wants("directive") and by_leverage:
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
                # Presence is counted separately from attribution. Every
                # ``split_file`` and ``break_cycle`` plan stores an empty
                # ``source_biomarker``, so keying "has plans" off the biomarker
                # set would report no plans on a file while the highest-leverage
                # plan kind sits on it.
                plan_count_by_path[path] = plan_count_by_path.get(path, 0) + 1
                if source:
                    plan_biomarkers_by_path.setdefault(path, set()).add(source)

    # KPIs keep test files in by default, and ``scope`` is what changes that.
    # The default is not arbitrary: measured across this workspace, dropping
    # test material moves NLOC-weighted ``average_health`` 7.52 -> 6.87 here,
    # 7.07 -> 6.27 on the backend repo and 7.59 -> 7.46 on the frontend. Tests
    # score better than production code, so a narrowed number is a lower number
    # with no defect having been found — a caller should ask for it knowingly.
    kpis = _compute_kpis(
        metric_rows if scoped else all_metrics,
        hotspot_paths=hotspot_paths,
        performance_findings=perf_findings_count,
        coverage=perf_coverage,
    )

    if scoped:
        metric_payload: list[dict[str, Any]] = []
        for m in metric_rows:
            row = _serialize_metric(m, leads.get(m.file_path), is_test=m.file_path in test_paths)
            if m.file_path in signals_by_path:
                row["signals"] = signals_by_path[m.file_path]
            metric_payload.append(row)
        module_rollup = _module_rollups(
            [m for m in all_metrics if m.module in set(module_targets)], deductions
        )
        result: dict[str, Any] = {
            "mode": "targets",
            "targets": raw_targets,
            # Deliberately NOT capped by ``limit``: the caller named these files
            # and getting back fewer than they asked about would answer a
            # different question. The response-size guard is what bounds it, and
            # ``metrics_total`` is the ``*_total`` sibling that makes a trim
            # visible — a ``module:`` target expands to every file in the module,
            # so this is the one growable list whose length the caller cannot
            # infer from what they passed.
            **({"modules": module_rollup} if module_targets else {}),
            "metrics": bounded(metric_payload, "metrics"),
            "metrics_total": len(metric_payload),
            # Capped like every other ranked list, with the total alongside so
            # the truncation is visible rather than inferred from the length.
            "findings": bounded(
                [_serialize_finding(f, reference_repository) for f in finding_rows],
                "findings",
            ),
            "findings_total": findings_total,
            # Which reading these scores are on, last so the identity keys keep
            # the head of the payload. Without it a projected score is
            # indistinguishable from the calibrated one.
            "scope": reported_scope,
            "counts": reported_counts,
        }
        unresolved = _unresolved_targets(
            file_targets=file_targets,
            module_targets=module_targets,
            matched_modules=matched_modules,
            resolved_paths={m.file_path for m in metric_rows},
            excluded_paths=excluded_paths,
            unscored_paths=unscored_paths,
            repo_root=ctx.path,
        )
        if unresolved:
            result["unresolved"] = unresolved
            if any(u["reason"] == "no_such_module" for u in unresolved):
                # ``module:`` has no discovery call of its own, so a bad name
                # would otherwise cost a full dashboard round-trip to correct.
                result["known_modules"] = sorted({m.module for m in all_metrics if m.module})
        # Per-file score trajectory for each target — silent (omitted) when a
        # file has < 2 snapshots of history rather than a misleading flat line.
        trends = []
        for m in metric_rows:
            t = file_trend(snapshots, m.file_path)
            if not t.points:
                continue
            series = [round(p.score, 2) for p in t.points]
            entry: dict[str, Any] = {
                "file_path": t.file_path,
                "series": series,
                "current": t.current,
                "delta": t.delta,
                "declining": t.declining,
            }
            # The score floors at 1.0, so a file deep enough to sit on it keeps
            # a flat series however much of the work gets done. Where a
            # snapshot recorded the real depth, carry the series that can still
            # move — and only there, so the files the floor never touches pay
            # nothing for it.
            unclamped = [round(p.unclamped_score, 2) for p in t.points]
            if unclamped != series:
                entry["unclamped_series"] = unclamped
                entry["unclamped_delta"] = t.unclamped_delta
            trends.append(entry)
        if trends:
            result["trends"] = bounded(trends, "trends")
        if module_targets and not only and not include_set:
            result["secondary_rankings"] = {
                "findings": {
                    "total": findings_total,
                    "call": (
                        f"get_health(targets={raw_targets!r}, only=['findings'], "
                        f"repo={repo!r}, limit=50, refactoring_view='{refactoring_view}')"
                    ),
                },
                "trends": {
                    "total": len(trends),
                    "call": (
                        f"get_health(targets={raw_targets!r}, only=['trends'], "
                        f"repo={repo!r}, limit=50, refactoring_view='{refactoring_view}')"
                    ),
                },
            }
            for block in ("findings", "trends"):
                result.pop(block, None)
                result.pop(f"{block}_total", None)
    else:
        # Dashboard mode — top-N worst files + headline findings + the
        # per-module rollup so the overview page doesn't need a second
        # round-trip. ``by_leverage`` is built above, before the leads.
        # Same serializer as worst_files, so every row carries
        # weighted_deficit for the caller to sort on further.
        all_modules = _module_rollups(all_metrics, deductions)
        gap = _gap_analysis(all_metrics)
        result = {
            # Lead with the call, not the data. Every block below ranks and
            # describes; this one recommends.
            "directive": _directive(
                by_leverage,
                leads,
                gap.get("weighted_gross_gap_points") or 0,
                plan_biomarkers_by_path,
                plan_count_by_path,
            ),
            # A second, additive lead for the performance pillar. The block
            # above is unchanged: performance findings carry no defect impact,
            # so they never competed for it and the dashboard said nothing an
            # agent could act on about them.
            # Two more additive leads, on the same terms: the block above ranks
            # files by health deficit and cannot say which composed work to do,
            # and it explicitly reports that no plan addresses the cause it
            # names on most files. These say what there is to do about it.
            **(
                {"refactoring_directive": refactoring.directive}
                if refactoring.directive is not None
                else {}
            ),
            **(
                {"performance_directive": performance.directive}
                if performance.directive is not None
                else {}
            ),
            "mode": "dashboard",
            "scope": reported_scope,
            "counts": reported_counts,
            "unscored_files": unscored_files,
            "kpis": kpis,
            "distribution": health_distribution(all_metrics),
            # Where the gap to the target concentrates — the "few files, not the
            # long tail" reframe that turns a repo-wide number into a short list.
            "gap_analysis": gap,
            "worst_files": bounded([
                _serialize_metric(m, leads.get(m.file_path), is_test=m.file_path in test_paths)
                for m in metric_rows
            ], "worst_files"),
            # Both ranked file lists deliberately keep test files in place, and
            # both now say which rows are tests. Measured on this repo, 0 of the
            # top 25 by the worst-first comparator are test material, so there
            # is no crowding here to fix — and dropping them would quietly
            # change which files the repo's "worst" are. The crowding is in the
            # *finding* lists, which is where the split below happens.
            "worst_files_total": len(metric_rows),
            # The one list whose entire purpose is leverage ranking, so it is the
            # one place ``weighted_deficit`` gets a denominator. The bare number
            # is score-points x NLOC and answers "which is bigger" but never "is
            # this worth doing"; the same quantity as a share of the repo's total
            # gap does, and it is the unit ``directive`` already speaks. The
            # denominator is the gross deficit of all below-target files, so a
            # share is bounded by 100% and the rows sum to 100% by construction
            # — the net gap would let above-target files cushion the total and push a
            # single large file over 100% (issue #1437).
            "high_leverage_files": bounded([
                {
                    **_serialize_metric(
                        m, leads.get(m.file_path), is_test=m.file_path in test_paths
                    ),
                    "share_of_repo_gap_pct": (
                        round(
                            100.0
                            * max(TARGET_SCORE - m.score, 0.0)
                            * max(m.nloc, 1)
                            / gap["weighted_gross_gap_points"],
                            1,
                        )
                        if gap.get("weighted_gross_gap_points")
                        else None
                    ),
                }
                for m in by_leverage
            ], "high_leverage_files"),
            "high_leverage_files_total": len(by_leverage),
            "top_findings": bounded(
                [_serialize_finding(f, reference_repository) for f in finding_rows],
                "top_findings",
            ),
            "top_findings_total": findings_total,
            # The test half of the same ranked set, in its own bucket so a
            # thrashing test suite stays visible without competing with
            # production defect risk for the most-read list.
            "test_findings": bounded(
                [_serialize_finding(f, reference_repository) for f in test_finding_rows],
                "test_findings",
            ),
            "test_findings_total": test_findings_total,
            # Worst-first, so the cap keeps the modules worth looking at. On a
            # monorepo the tail is dozens of single-file buckets.
            "modules": bounded(all_modules, "modules"),
            "modules_total": len(all_modules),
        }
        if not only:
            result["secondary_rankings"] = {
                "worst_files": {
                    "total": len(metric_rows),
                    "call": f"get_health(repo={repo!r}, only=['worst_files'], limit=50)",
                },
                "top_findings": {
                    "total": findings_total,
                    "call": f"get_health(repo={repo!r}, only=['top_findings'], limit=50)",
                },
                "test_findings": {
                    "total": test_findings_total,
                    "call": f"get_health(repo={repo!r}, only=['test_findings'], limit=50)",
                },
                "modules": {
                    "total": len(all_modules),
                    "call": f"get_health(repo={repo!r}, only=['modules'], limit=50)",
                },
            }
            for block in ("worst_files", "top_findings", "test_findings", "modules"):
                result.pop(block, None)
                result.pop(f"{block}_total", None)
        if "churn_complexity" in include_set:
            result["churn_complexity"] = bounded(churn_points, "churn_complexity")
        if "accuracy" in include_set:
            # Self-validation: does the score rank the buggy files first?
            # Scored over the full open set (``accuracy_rows``), not the capped
            # head — ranking quality measured on the top 20 would be circular.
            # ``None`` when there isn't enough signal for an honest number.
            result["defect_accuracy"] = compute_defect_accuracy(
                all_metrics,
                [_serialize_finding(f, reference_repository) for f in accuracy_rows],
                # The same map ``all_metrics`` was ranked with, so the stat
                # measures exactly the ``worst_files`` this response printed.
                deductions=deductions,
            )

    if "biomarkers" in include_set and "findings" not in result:
        # Capped like every other ranked list. Uncapped, this was the one block
        # in the tool that could return the repo's entire open finding set: on a
        # 3.2k-file repo ``include=["biomarkers"]`` with no targets served 10.3k
        # rows / 4.7MB, which overflows an agent's context and returns nothing
        # usable. Findings arrive impact-ordered, so the cap keeps the ones
        # worth reading.
        result["findings"] = bounded(
            [_serialize_finding(f, reference_repository) for f in finding_rows],
            "findings",
        )
        result["findings_total"] = findings_total
        # Same production/test split as ``top_findings``: this block only ever
        # fires in dashboard mode (targeted mode set ``findings`` above), so it
        # is describing the repo, not a file the caller named.
        result["test_findings"] = bounded(
            [_serialize_finding(f, reference_repository) for f in test_finding_rows],
            "test_findings",
        )
        result["test_findings_total"] = test_findings_total

    if plans_requested:
        # Canonical is the shared REST/MCP/CLI order.  File diversity remains
        # available only through the explicitly named ``file_spread`` view.
        validation_profiles: dict[str, dict[str, Any]] = {}
        plan_payload = []
        selected_recommendations = bounded(
            refactoring_recommendations,
            "refactoring_plans",
            cap=min(limit, 6),
        )
        for recommendation in selected_recommendations:
            payload = _serialize_refactoring(recommendation, reference_repository)
            validation = payload.pop("validation", None)
            if validation:
                profile_id, profile = _validation_profile(validation)
                validation_profiles.setdefault(profile_id, profile)
                payload["validation_profile_id"] = profile_id
            plan_payload.append(payload)
        result["refactoring_plans"] = plan_payload
        if validation_profiles:
            result["validation_profiles"] = list(validation_profiles.values())
            _stamp_collection(
                result,
                "validation_profiles",
                total=len(validation_profiles),
                reason="profile_cap",
            )
        result["refactoring_plans_total"] = len(refactoring_rows)
        if wants("refactoring_plans"):
            finding_for_action = next(iter(action_finding_rows), None)
            result["refactoring_plans_status"] = _refactoring_plans_status(
                available_plans_total=len(refactoring_recommendations),
                plans_emitted=len(plan_payload),
                scoped=scoped,
                has_eligible_metrics=bool(metric_rows if scoped else all_metrics),
                finding=finding_for_action,
                repo=repo,
            )
        # The deterministic prose suggestion is the fallback for biomarkers
        # that have no structured detector yet. It is emitted once per
        # biomarker type as ``suggestion_legend`` (built below, after the
        # dimension filter) rather than copied onto every finding: the text is
        # keyed purely by type, so the per-row form repeated one ~40-word
        # string up to 10x in a single response.
        #
        # (The old no-findings-anywhere fallback here was unreachable: targeted
        # mode always sets ``findings`` and dashboard mode always sets
        # ``top_findings``.)

    if "trend" in include_set:
        summary = diff_snapshots(snapshots)
        narrowed = reported_scope == "production"
        recent = recent_kpis(snapshots, limit=10)
        if narrowed:
            recent = drop_unscoped_fields(recent)
        alerts = [
            {
                "kind": a.kind,
                "metric": a.metric,
                "current": a.current,
                "baseline": a.baseline,
                "delta": a.delta,
                "message": a.message,
                "driver": a.driver,
                "structure_delta": a.structure_delta,
                "history_delta": a.history_delta,
            }
            for a in summary.alerts
        ]
        # The hotspot pair describes the whole repository whatever the scope,
        # since only the average was snapshotted for both populations.
        result["trend"] = {
            "current_hotspot_health": None if narrowed else summary.current_hotspot_health,
            "current_average_health": summary.current_average_health,
            "previous_hotspot_health": None if narrowed else summary.previous_hotspot_health,
            "previous_average_health": summary.previous_average_health,
            "hotspot_delta": None if narrowed else summary.hotspot_delta,
            "average_delta": summary.average_delta,
            "current_structure_deduction": summary.current_structure_deduction,
            "current_history_deduction": summary.current_history_deduction,
            "alerts": bounded(alerts, "trend.alerts"),
            "alerts_total": len(alerts),
            "alerts_emitted": min(len(alerts), limit),
            "recent": bounded(recent, "trend.recent"),
            "recent_total": len(recent),
            "recent_emitted": min(len(recent), limit),
        }
        if len(alerts) > limit:
            result["trend"]["alerts_reduced_reason"] = "limit"
        if len(recent) > limit:
            result["trend"]["recent_reduced_reason"] = "limit"

    if refactoring.page is not None and wants("refactoring_opportunities"):
        result["refactoring_opportunities"] = refactoring.page.items
        result["refactoring_opportunities_total"] = refactoring.page.total
        result["refactoring_opportunities_emitted"] = len(refactoring.page.items)
        if len(refactoring.page.items) < refactoring.page.total:
            result["refactoring_opportunities_reduced_reason"] = (
                "collection_cap" if limit > _REFACTORING_COLLECTION_CAP else "limit"
            )
        if refactoring.page.next_offset is not None:
            page_recoveries["refactoring_opportunities"] = (
                refactoring.page.next_offset,
                _REFACTORING_COLLECTION_CAP,
                refactoring.page.total - refactoring.page.next_offset,
            )
        if refactoring.ignored:
            result["ignored_arguments"] = {
                **result.get("ignored_arguments", {}),
                **refactoring.ignored,
            }

    if refactoring.summary is not None:
        result["refactoring_summary"] = {
            **refactoring.summary,
            "facets": refactoring.page.facets if refactoring.page else {},
            "view": refactoring_view,
            "next_call": (
                "get_health(include=['refactoring'], "
                "only=['refactoring_opportunities'], limit=6)"
            ),
        }

    if performance.page is not None and wants("performance_opportunities"):
        result["performance_opportunities"] = performance.page.items
        result["performance_opportunities_total"] = performance.page.total
        result["performance_opportunities_emitted"] = len(performance.page.items)
        if performance.page.next_offset is not None:
            page_recoveries["performance_opportunities"] = (
                performance.page.next_offset,
                _PERFORMANCE_COLLECTION_CAP,
                performance.page.total - performance.page.next_offset,
            )
        if performance.ignored:
            result["ignored_arguments"] = {
                **result.get("ignored_arguments", {}),
                **performance.ignored,
            }

    if performance.summary is not None:
        result["performance_summary"] = {
            **performance.summary,
            "facets": performance.page.facets if performance.page else {},
            "next_call": (
                "get_health(include=['performance'], "
                "only=['performance_opportunities'], limit=6)"
            ),
        }

    if {"performance", "refactoring"} <= include_set and wants("recommendation_lede"):
        performance_lead = next(
            (
                item
                for item in (performance.page.items if performance.page else [])
                if item.get("biomarker_type") not in NON_LEADING_MARKERS
            ),
            None,
        )
        recommendation_lead = (
            refactoring_recommendations[0] if refactoring_recommendations else None
        )
        lead_payload = (
            _serialize_refactoring(recommendation_lead, reference_repository)
            if recommendation_lead
            else None
        )
        result["recommendation_lede"] = {
            "performance_opportunities_total": (
                performance.page.total if performance.page else 0
            ),
            "refactoring_plans_total": len(refactoring_recommendations),
            "performance_lead": (
                {
                    key: performance_lead[key]
                    for key in (
                        "opportunity_id",
                        "intervention_symbol",
                        "boundary_kind",
                        "execution_context",
                        "affected_call_sites_total",
                        "rank_score",
                    )
                }
                if performance_lead
                else None
            ),
            "recommendation_lead": (
                {
                    key: lead_payload[key]
                    for key in (
                        "id",
                        "refactoring_type",
                        "file_path",
                        "target_symbol",
                        "benefit",
                        "leverage",
                        "cost",
                        "risk",
                        "rank_score",
                    )
                }
                if lead_payload
                else None
            ),
            # The exact plan for the exact lead, from the one place that decides
            # plan linkage. This used to match on a key the plan writer never
            # wrote, so it was unconditionally null.
            "performance_plan_id": (
                performance_lead["plan_reference"] if performance_lead else None
            ),
            "performance_plan_reason": (
                performance_lead["plan_reason"] if performance_lead else None
            ),
            "next_call": (
                f"get_health(targets={raw_targets!r}, repo={repo!r}, "
                "include=['performance','refactoring'], limit=3, "
                "only=['performance_opportunities','refactoring_plans'], "
                f"refactoring_view='{refactoring_view}')"
            ),
        }

    if "coverage" in include_set:
        # Drop the bulky covered-lines arrays from dashboard mode; full
        # detail is available in targeted mode.
        if scoped:
            selected_coverage = bounded(coverage_rows, "coverage.files")
            coverage_payload = [_serialize_coverage_row(r) for r in selected_coverage]
            _attach_coverage_decay(coverage_payload, selected_coverage, str(ctx.path))
        else:
            # Built narrow, not built wide and subtracted from. These rows came
            # back without the column at all (see the read above).
            full_coverage_payload = [
                _serialize_coverage_row(r, covered_lines=False) for r in coverage_rows
            ]
            coverage_payload = bounded(full_coverage_payload, "coverage.files")
        # ``ingested_at`` is a datetime on the summary too — coerce.
        if coverage_summary.get("ingested_at") is not None:
            coverage_summary = {
                **coverage_summary,
                "ingested_at": coverage_summary["ingested_at"].isoformat(),
            }
        result["coverage"] = {
            "summary": coverage_summary,
            "files": coverage_payload,
            "files_total": len(coverage_rows),
            "files_emitted": len(coverage_payload),
        }
        if len(coverage_payload) < len(coverage_rows):
            result["coverage"]["files_reduced_reason"] = "limit"

    if "doc_drift" in include_set:
        if drift_unavailable is not None:
            result["doc_drift"] = {"unavailable": drift_unavailable}
        else:
            drift_payload = bounded(
                # ``evidence=False``: its first line restates ``file_path``,
                # ``line_number`` and ``raw``, and the rest is the resolver's
                # own trace, which is a poor trade against this budget. The
                # CLI, which has no budget, keeps it.
                [serialize_doc_drift_row(r, evidence=False) for r in drift_rows],
                "doc_drift.findings",
            )
            result["doc_drift"] = {
                "findings": drift_payload,
                "findings_total": len(drift_rows),
                "findings_emitted": len(drift_payload),
                "documents": len({r.file_path for r in drift_rows}),
                "confidence": summarize_confidence_rows(drift_rows),
                # The same sentence the CLI prints, under the house ``*_basis``
                # name for "what this count does and does not cover". A surface
                # that shows findings without it claims coverage and precision
                # this detector does not have: most references are uncheckable.
                "findings_basis": DETECTION_BASIS,
            }
            if len(drift_payload) < len(drift_rows):
                result["doc_drift"]["findings_reduced_reason"] = "limit"

    # (The dimension filter — ``include=["performance"]`` and friends, so an
    # agent can ask "show me only the performance risk in this change" — is
    # applied where the rows are selected, not here. Filtering the finished
    # response meant filtering a list already capped by impact.)

    # One entry per biomarker type actually present in the findings this
    # response carries. Built last so the dimension filter above has already
    # narrowed the rows the caller will join against.
    if "refactoring" in include_set and wants("suggestion_legend"):
        # Built from the ranked rows themselves, not from the serialized blocks
        # in ``result``. It used to read ``result["findings"]`` /
        # ``["top_findings"]``, which the ``only`` projection's ``wants()``
        # gating can skip building — so
        # ``only=["refactoring_plans","suggestion_legend"]`` returned an empty
        # legend and adding ``top_findings`` back to ``only`` refilled it. A
        # projection is supposed to subtract keys, never change what a surviving
        # key contains.
        #
        # Scope note, and it is a real limitation rather than an oversight: the
        # legend explains the *findings*, while it ships beside
        # ``refactoring_plans``. Those are different sets — no plan kind is
        # sourced from ``coverage_gradient``, the lead biomarker on this repo's
        # ten worst files — so a legend entry can describe a biomarker the plans
        # do not address. ``directive.plan_addresses_reason`` is what reports
        # that mismatch; the legend is not the place to paper over it.
        present_types = {getattr(r, "biomarker_type", None) for r in legend_rows}
        result["suggestion_legend"] = {
            bt: suggestion_for(bt) for bt in sorted(t for t in present_types if t)
        }

    collection_totals = {
        "targets": len(raw_targets),
        "metrics": len(metric_payload) if scoped else None,
        "findings": findings_total,
        "trends": len(trends) if scoped else None,
        "modules": len(module_rollup) if scoped else len(all_modules),
        "worst_files": len(metric_rows),
        "high_leverage_files": len(by_leverage),
        "top_findings": findings_total,
        "test_findings": test_findings_total,
        "churn_complexity": len(churn_points),
        "refactoring_plans": len(refactoring_recommendations),
        "performance_opportunities": (
            performance.page.total if performance.page is not None else 0
        ),
    }
    for key, total in collection_totals.items():
        if total is None:
            continue
        cap_reason = (
            "collection_cap"
            if key in {"refactoring_plans", "performance_opportunities"}
            and limit > 6
            and len(result.get(key, [])) == 6
            else "limit"
        )
        _stamp_collection(result, key, total=total, reason=cap_reason)
    if unknown_include_keys:
        result["unknown_include_keys"] = unknown_include_keys
    _stamp_nested_collections(result)

    # A misspelled control falls back to the default, answering a different
    # question under the name the caller asked for. The routes and the CLI
    # reject outright; MCP cannot, so it names the value it dropped.
    rejected = {
        name: raw
        for name, raw, resolved in (
            ("scope", scope, reported_scope),
            ("counts", counts, reported_counts),
        )
        if raw is not None and raw != resolved
    }
    if rejected:
        result["ignored_arguments"] = {**result.get("ignored_arguments", {}), **rejected}

    # Snapshots and the two ranked queues have no stored split to re-read, so
    # they stay calibrated. Named, or a projected headline reads as if
    # everything beside it were projected too.
    if reported_counts == "code_shape":
        unprojected = [
            key
            for key in ("trend", "trends", "refactoring_opportunities", "performance_opportunities")
            if key in result
        ]
        if unprojected:
            result["counts_not_applied_to"] = unprojected

    # Projection. ``include`` could only ever add blocks, so asking for one
    # extra block re-shipped the whole dashboard with it; ``only`` is the
    # subtract half. Applied last so it can drop anything above, and ``mode`` /
    # ``_meta`` always survive — a response the caller cannot orient in is not
    # a saving.
    if only:
        # Every capped list's ``*_total`` sibling survives with it. The tool
        # documents "each carries a ``*_total`` sibling so truncation is never
        # silent", and the projection was quietly breaking exactly that promise:
        # ``only=["modules"]`` at ``limit=50`` returned 50 of 116 modules with
        # no ``modules_total`` to say so. Retaining it is not the caller's job —
        # a caller who knew to ask for the total would not need the guarantee.
        # ``unresolved`` / ``known_modules`` survive any projection, for the same
        # reason ``mode`` does. They are the block that stops an empty result
        # reading as "this file is healthy" (A1), and projecting them away put a
        # typo'd target straight back to silent: ``targets=["does/not/exist.py"],
        # only=["metrics"]`` returned ``metrics: []`` and nothing else. A caller
        # who has to ask for the error report in order to see it does not have an
        # error report.
        keep = (
            set(only_list)
            | {
                "mode",
                "targets",
                "targets_total",
                "targets_emitted",
                "unresolved",
                "unresolved_total",
                "unresolved_emitted",
                "known_modules",
                "known_modules_total",
                "known_modules_emitted",
                "unknown_include_keys",
                "unknown_include_keys_total",
                "unknown_include_keys_emitted",
                # A rejected filter value is a caller-error report, so it
                # survives a projection for the same reason ``unresolved`` does.
                "ignored_arguments",
                "recovery",
            }
            | {
                suffix
                for k in only_list
                for suffix in (
                    f"{k}_total",
                    f"{k}_emitted",
                    f"{k}_reduced_reason",
                )
            }
        )
        # Which reading the kept numbers are on, but only once it is not the
        # default: unconditionally would add three empty keys to every other
        # projection.
        if reported_scope != DEFAULT_SCOPE:
            keep |= {"scope"}
        if reported_counts != DEFAULT_COUNTS:
            keep |= {"counts", "unscored_files", "counts_not_applied_to"}
        if "refactoring_plans" in only_set:
            keep |= {
                "refactoring_plans_status",
                "validation_profiles",
                "validation_profiles_total",
                "validation_profiles_emitted",
                "validation_profiles_reduced_reason",
            }
        # A key that does not exist in this response is named rather than
        # quietly yielding an empty one — same rule as ``unresolved`` above.
        # A misspelled projection is otherwise indistinguishable from a block
        # the repo genuinely has no data for. Reported against what the caller
        # actually passed, so an alias resolving to a present key is not "unknown".
        unknown = sorted(
            raw for raw, resolved in zip(only, only_list, strict=True) if resolved not in result
        )
        result = {k: v for k, v in result.items() if k in keep}
        if unknown:
            result["unknown_only_keys"] = unknown
            _stamp_collection(result, "unknown_only_keys", total=len(unknown))

    visible_recoveries = {
        label: values
        for label, values in page_recoveries.items()
        if label.split(".", 1)[0] in result
    }
    if visible_recoveries:
        recovery: dict[str, dict[str, Any]] = {}
        for label, (next_cursor, next_limit, remaining) in visible_recoveries.items():
            root = label.split(".", 1)[0]
            recovery[label] = {
                "remaining": remaining,
                "call": (
                    f"get_health(targets={raw_targets!r}, include={list(include or [])!r}, "
                    f"repo={repo!r}, limit={next_limit}, only={[root]!r}, "
                    f"refactoring_view='{refactoring_view}', cursor={next_cursor})"
                ),
            }
        result["recovery"] = recovery

    # Targeted mode scopes the stale signal to the asked-about files; the
    # dashboard (no targets) keeps the repo-level warning.
    result["_meta"] = _build_meta(repository=repository, targets=targets if targets else None)
    analyzed_source = metric_rows if scoped else all_metrics
    if scoped:
        # Scoped calls used to answer repository freshness from the caller's own
        # files, so one repo read two different statuses in the same second
        # depending on which mode answered.
        await _attach_repository_analysis_meta(session, repository, result["_meta"])
    else:
        _attach_health_analysis_meta(result["_meta"], analyzed_source)
    # Server-side wall clock, as ``get_context`` already reports. Without it a
    # regression in here is invisible until someone profiles it by hand.
    for label, dropped in semantic_omissions.items():
        root = label.split(".", 1)[0]
        if root in result:
            omission_collector.add(
                f"{label} beyond emitted cap ({len(dropped)} dropped)",
                [
                    _serialize_refactoring(row, reference_repository)
                    if isinstance(row, Recommendation)
                    else row.as_dict()
                    if hasattr(row, "as_dict")
                    else row
                    for row in dropped
                ],
            )
    omission_collector.attach(result)
    result["_meta"]["timing_ms"] = round((perf_counter() - started) * 1000, 2)
    return result
