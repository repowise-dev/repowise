"""MCP tool: get_health — code-health markers and per-file scores.

A thin dispatcher: a lookup by id answers from ``details``; everything else
reads once (``loading``), builds the mode's payload (``targeted`` /
``dashboard``), adds the ``include``-gated blocks (``blocks``), and applies the
accounting and projection passes (``paging`` / ``projection``).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from time import perf_counter
from typing import Any

from repowise.core.analysis.finding_registry import (
    FILE_HOLDS,
    GateLayer,
    served_previews,
    withheld_summary,
)
from repowise.core.analysis.health.counts import DEFAULT_COUNTS
from repowise.core.analysis.health.refactoring.serving import (
    DEFAULT_VIEW as _REFACTORING_VIEW_DEFAULT,
)
from repowise.core.analysis.health.scope import DEFAULT_SCOPE
from repowise.core.analysis.health.semantics import health_semantics_contract
from repowise.core.persistence.database import get_session
from repowise.core.registry import ToolRecipe
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._workspace_routing import (
    hint_misses,
    needs_routing,
    one_repo_refusal,
    route_targets,
    single_repo,
)
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

__all__ = ["_ONLY_ALIASES", "get_health"]


@mcp.tool(
    surface_order=90,
    artifact_type="health",
    presentation="health",
    evidence_basis="measured",
    recipes=(
        ToolRecipe(
            "health_fix_first",
            'get_health(only=["fix_first"])',
            ("get_health",),
        ),
        ToolRecipe(
            "health_fix_item",
            'get_health(fix_id="fix1_...")',
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
    limit: int | None = None,
    only: list[str] | None = None,
    refactoring_view: str = _REFACTORING_VIEW_DEFAULT,
    refactoring_type: str | None = None,
    refactoring_confidence: str | None = None,
    refactoring_effort: str | None = None,
    refactoring_scope: str | None = None,
    cursor: int = 0,
    finding_id: str | None = None,
    plan_id: str | None = None,
    opportunity_id: str | None = None,
    fix_id: str | None = None,
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

    No ``targets``: a dashboard led by ``fix_first``, what to fix first;
    targets rank files and findings.
    Never recomputes: commit, then run ``repowise update``.
    Every block and accepted value: docs/agent/MCP_TOOLS.md.

    Args:
        targets: file paths or ``module:<name>``; misses land in ``unresolved``.
        include: ``biomarkers``|``refactoring``|``trend``|``coverage``|
            ``accuracy``|``signals``|``churn_complexity``|``doc_drift``|
            ``semantics``|``recipe``|``what_if``, or a dimension incl. ``advisory``;
            ``performance`` and ``refactoring`` add queues.
        only: keys to keep; identity, totals, recovery survive.
            ``biomarkers``/``accuracy``/``refactoring`` alias their block key;
            ``performance``/``defect``/``maintainability``/``advisory``
            do not: they filter rows into ``unknown_only_keys``.
        repo: usually omitted.
        limit: max rows per ranked list (default 20), ``0`` for none.
        cursor: offset into a ranked list.
        fix_id/finding_id/plan_id: stable ``id``: item or ``act_``, finding, plan.
        opportunity_id: ``perf...``/``refop...``: the unit, its steps or
            plan, evidence paged by ``only=["*_evidence"]``.
        refactoring_view: ``diversified`` (default)|``canonical``|
            ``file_spread``; _type/_confidence/_effort/_scope filter.
        performance_view/_context/_boundary/_confidence/_actionability/_sort:
            queue filters.
        scope / counts: default ``all``/``everything``. ``production`` drops
            test files; ``code_shape`` drops git-derived score and findings.

    """
    started = perf_counter()
    conflict = _selector_conflict(
        fix_id=fix_id, finding_id=finding_id, plan_id=plan_id, opportunity_id=opportunity_id
    )
    if conflict is not None:
        return _note_inapplicable_controls(conflict, scope, counts)
    routes = await route_targets(targets, paths_only=True) if repo is None and targets else None
    routed_repo = None
    if needs_routing(routes):
        routed_repo = single_repo(routes)
        if routed_repo is None:
            return {**one_repo_refusal("get_health", routes), "_meta": _build_meta()}
        targets, repo = [r.local for r in routes], routed_repo
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
        refactoring_scope=refactoring_scope,
        performance_view=performance_view,
        performance_context=performance_context,
        performance_boundary=performance_boundary,
        performance_confidence=performance_confidence,
        performance_actionability=performance_actionability,
        performance_sort=performance_sort,
        scope=scope,
        counts=counts,
    )
    if repo == "all":
        return _unsupported_repo_all("get_health")
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
            fix_id=fix_id,
            only_set=req.only_set,
            limit=req.limit,
            cursor=req.cursor,
            recipe="recipe" in req.include_set,
        )
        if detail is not None:
            _attach_semantics(detail, req)
            return _note_inapplicable_controls(detail, scope, counts)
        data = await load_health_data(
            session, repository, reference_repository, ctx.path, req
        )
        # The rest stays inside the session: the analysis meta queries it, and a
        # query on a closed session checks out a connection nothing returns.
        if data.pop.scoped:
            # A missing target is explained from git history (a sync subprocess
            # per miss), so keep it off the event loop.
            result, mode_totals = await asyncio.to_thread(
                build_targeted, data, req, pager, ctx.path
            )
        else:
            result, mode_totals = build_dashboard(data, req, pager)
        add_optional_blocks(result, data, req, pager, str(ctx.path))
        result = _finish(result, data, req, pager, mode_totals)

        # Targeted mode scopes the stale signal to the asked-about files.
        result["_meta"] = _build_meta(repository=repository, targets=targets if targets else None)
        if data.pop.scoped:
            # Analysis freshness is repo-wide, so both modes report the same status.
            await _attach_repository_analysis_meta(session, repository, result["_meta"])
        else:
            _attach_health_analysis_meta(result["_meta"], data.pop.all_metrics)
        _attach_gated(result["_meta"], data)
        _attach_preview(result)
        _attach_withheld(result, data)
        pager.report_omissions(result, omission_collector)
        omission_collector.attach(result)
        _attach_semantics(result, req)
        # Server-side wall clock, as ``get_context`` reports.
        result["_meta"]["timing_ms"] = round((perf_counter() - started) * 1000, 2)
        if routed_repo is not None:
            if any(r.routed for r in routes):
                result["repo"] = routed_repo
            hint_misses(result, routes)
        return result


def _attach_gated(meta: dict[str, Any], data: HealthData) -> None:
    """What a language gate or file hold held back, in ``_meta`` so no ``only``
    projection drops it: an agent must read "held back", never "nothing found"."""
    if not data.gated:
        return
    # A file hold is a count per key: its reason is one fixed sentence, sent
    # once under ``reasons`` and only when the call lists no finding, so a
    # default response grows by a few tokens at most.
    gated: dict[str, Any] = {
        layer: {k: v["count"] if k in FILE_HOLDS else v for k, v in cells.items()}
        for layer, cells in data.gated.items()
    }
    held = sorted({k for cells in data.gated.values() for k in cells if k in FILE_HOLDS})
    if held and not data.findings.emitted:
        gated["reasons"] = {k: FILE_HOLDS[k] for k in held}
    meta["gated"] = {**gated, "opt_in": {"include": ["unverified"]}}


#: The lists that carry rows of a gated layer: ``(layer, the row's kind field)``.
#: A file row (its score and lead) is health's.
_LAYER_LISTS: dict[str, tuple[GateLayer, str]] = {
    "findings": ("health", "biomarker_type"),
    "top_findings": ("health", "biomarker_type"),
    "test_findings": ("health", "biomarker_type"),
    "metrics": ("health", "primary_biomarker"),
    "worst_files": ("health", "primary_biomarker"),
    "test_worst_files": ("health", "primary_biomarker"),
    "high_leverage_files": ("health", "primary_biomarker"),
    "refactoring_plans": ("refactoring", "refactoring_type"),
    "refactoring_opportunities": ("refactoring", "lead_refactoring_type"),
    "performance_opportunities": ("performance", "biomarker_type"),
}

#: A Fix-first item's kind, as the layer it was built from.
_FIX_KIND_LAYER: dict[str, GateLayer] = {
    "finding": "health",
    "refactor": "refactoring",
    "perf_fix": "performance",
}


def _fix_first_rows(result: dict[str, Any]) -> dict[GateLayer, list[dict[str, Any]]]:
    """The listed Fix-first items (lead included) as ``file_path`` rows by layer."""
    block = result.get("fix_first")
    if not isinstance(block, dict):
        return {}
    items = [*(block.get("items") or []), block.get("lead")]
    out: dict[GateLayer, list[dict[str, Any]]] = {}
    for item in items:
        if isinstance(item, dict) and (layer := _FIX_KIND_LAYER.get(item.get("kind") or "")):
            path = (item.get("target") or {}).get("file_path")
            out.setdefault(layer, []).append({"file_path": path})
    return out


def _attach_preview(result: dict[str, Any]) -> None:
    """``_meta.preview``: ``{layer: {language: note}}`` for the preview cells
    this response lists rows from, read after projection so only what was
    sent is labelled. Absent when none, so a default response does not grow."""
    sources: list[tuple[GateLayer, str, list[Any]]] = [
        (layer, kind, rows)
        for key, (layer, kind) in _LAYER_LISTS.items()
        if isinstance(rows := result.get(key), list)
    ]
    sources += [(layer, "kind", rows) for layer, rows in _fix_first_rows(result).items()]
    preview: dict[str, dict[str, str]] = {}
    for layer, kind, rows in sources:
        served = served_previews(
            layer,
            (r for r in rows if isinstance(r, dict)),
            kind=kind,
            get=lambda r, k: r.get(k),
        )
        if served:
            preview.setdefault(layer, {}).update(served)
    if preview:
        result["_meta"]["preview"] = preview


def _attach_withheld(result: dict[str, Any], data: HealthData) -> None:
    """The withheld types behind the ``withheld_deduction`` of each emitted
    file row, so none is unexplained. Absent when no emitted row carries one."""
    paths = {
        row["file_path"]
        for rows in result.values()
        if isinstance(rows, list)
        for row in rows
        if isinstance(row, dict) and "withheld_deduction" in row
    }
    counts = Counter(
        r.biomarker_type for r in data.withheld_rows if r.health_impact and r.file_path in paths
    )
    if counts:
        result["_meta"]["withheld"] = withheld_summary(counts, counts)


def _attach_semantics(result: dict[str, Any], req: HealthRequest) -> None:
    """The unit legend, on request: it is the same on every call."""
    if "semantics" in req.include_set:
        result.setdefault("_meta", {})["health_semantics"] = health_semantics_contract()


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
            "test_worst_files": len(data.test_metric_rows),
            "high_leverage_files": len(data.by_leverage),
            "top_findings": findings.findings_total,
            "test_findings": findings.test_findings_total,
            "churn_complexity": len(data.churn_points),
            "refactoring_plans": (
                data.refactoring_plans.total if data.refactoring_plans is not None else 0
            ),
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
