"""MCP tool: get_health — code-health markers and per-file scores.

A thin dispatcher: a lookup by id answers from ``details``; everything else
reads once (``loading``), builds the mode's payload (``targeted`` /
``dashboard``), adds the ``include``-gated blocks (``blocks``), and applies the
accounting and projection passes (``paging`` / ``projection``).
"""

from __future__ import annotations

from time import perf_counter
from typing import Any

from repowise.core.analysis.health.counts import DEFAULT_COUNTS
from repowise.core.analysis.health.scope import DEFAULT_SCOPE
from repowise.core.persistence.database import get_session
from repowise.core.registry import ToolRecipe
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._helpers import _get_repo, _resolve_repo_context
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server.tool_health.analysis_meta import (
    _attach_health_analysis_meta,
    _attach_repository_analysis_meta,
)
from repowise.server.mcp_server.tool_health.blocks import add_optional_blocks
from repowise.server.mcp_server.tool_health.dashboard import build_dashboard
from repowise.server.mcp_server.tool_health.details import (
    _detail_response,
    _note_inapplicable_controls,
    _selector_conflict,
)
from repowise.server.mcp_server.tool_health.loading import HealthData, load_health_data
from repowise.server.mcp_server.tool_health.paging import (
    Pager,
    _stamp_collection_totals,
    _stamp_nested_collections,
)
from repowise.server.mcp_server.tool_health.projection import (
    _name_rejected_controls,
    _name_uncounted_blocks,
    _project,
)
from repowise.server.mcp_server.tool_health.request import _ONLY_ALIASES, HealthRequest
from repowise.server.mcp_server.tool_health.targeted import ModeTotals, build_targeted
from repowise.server.services.refactoring_health import DEFAULT_VIEW as _REFACTORING_VIEW_DEFAULT

__all__ = ["_ONLY_ALIASES", "get_health"]


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
    req = HealthRequest(
        targets=targets,
        include=include,
        only=only,
        repo=repo,
        limit=limit,
        cursor=cursor,
        refactoring_view=refactoring_view,
        refactoring_type=refactoring_type,
        refactoring_confidence=refactoring_confidence,
        refactoring_effort=refactoring_effort,
        performance_view=performance_view,
        performance_context=performance_context,
        performance_boundary=performance_boundary,
        performance_confidence=performance_confidence,
        performance_actionability=performance_actionability,
        performance_sort=performance_sort,
        scope=scope,
        counts=counts,
    )
    ctx = await _resolve_repo_context(repo)
    omission_collector = OmissionCollector("get_health", repo_root=ctx.path)
    pager = Pager(req.limit, req.cursor)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        reference_repository = ctx.alias or repository.name
        detail = await _detail_response(
            session,
            repository,
            reference_repository,
            finding_id=finding_id,
            plan_id=plan_id,
            opportunity_id=opportunity_id,
            only_set=req.only_set,
            limit=req.limit,
            cursor=req.cursor,
        )
        if detail is not None:
            return _note_inapplicable_controls(detail, scope, counts)
        data = await load_health_data(
            session, repository, reference_repository, ctx.path, req
        )

    if data.pop.scoped:
        result, mode_totals = build_targeted(data, req, pager, ctx.path)
    else:
        result, mode_totals = build_dashboard(data, req, pager)
    add_optional_blocks(result, data, req, pager, str(ctx.path))
    result = _finish(result, data, req, pager, mode_totals)

    # Targeted mode scopes the stale signal to the asked-about files; the
    # dashboard (no targets) keeps the repo-level warning.
    result["_meta"] = _build_meta(repository=repository, targets=targets if targets else None)
    if data.pop.scoped:
        # Scoped calls used to answer repository freshness from the caller's own
        # files, so one repo read two different statuses in the same second
        # depending on which mode answered.
        await _attach_repository_analysis_meta(session, repository, result["_meta"])
    else:
        _attach_health_analysis_meta(result["_meta"], data.pop.all_metrics)
    pager.report_omissions(result, omission_collector, reference_repository)
    omission_collector.attach(result)
    # Server-side wall clock, as ``get_context`` already reports. Without it a
    # regression in here is invisible until someone profiles it by hand.
    result["_meta"]["timing_ms"] = round((perf_counter() - started) * 1000, 2)
    return result


def _finish(
    result: dict[str, Any],
    data: HealthData,
    req: HealthRequest,
    pager: Pager,
    mode_totals: ModeTotals,
) -> dict[str, Any]:
    """Accounting, caller-error reports, the ``only`` projection, then recovery."""
    findings = data.findings
    _stamp_collection_totals(
        result,
        {
            "targets": len(req.raw_targets),
            "metrics": mode_totals.metrics,
            "findings": findings.findings_total,
            "trends": mode_totals.trends,
            "modules": mode_totals.modules,
            "worst_files": len(data.metric_rows),
            "high_leverage_files": len(data.by_leverage),
            "top_findings": findings.findings_total,
            "test_findings": findings.test_findings_total,
            "churn_complexity": len(data.churn_points),
            "refactoring_plans": len(data.refactoring_recommendations),
            "performance_opportunities": (
                data.performance.page.total if data.performance.page is not None else 0
            ),
        },
        req.limit,
    )
    if req.unknown_include_keys:
        result["unknown_include_keys"] = req.unknown_include_keys
    _stamp_nested_collections(result)
    reported_scope, reported_counts = data.pop.reported_scope, data.pop.reported_counts
    _name_rejected_controls(result, req, reported_scope, reported_counts)
    _name_uncounted_blocks(result, reported_counts)
    result = _project(result, req, reported_scope, reported_counts)
    recovery = pager.recovery_block(result, req)
    if recovery:
        result["recovery"] = recovery
    return result
