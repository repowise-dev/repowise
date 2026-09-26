"""Performance and refactoring pillar blocks for get_health: the queue, rollup and lead."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from repowise.core.analysis.health.perf.opportunity_rank import NON_LEADING_MARKERS
from repowise.core.analysis.health.refactoring.recommendations import Recommendation
from repowise.server.mcp_server.tool_health.paging import Pager
from repowise.server.mcp_server.tool_health.request import HealthRequest
from repowise.server.mcp_server.tool_health.serialize import _serialize_refactoring
from repowise.server.services.performance_health import (
    PerformanceHealthService,
    PerformancePage,
    parse_query,
)
from repowise.server.services.refactoring_health import RefactoringHealthService
from repowise.server.services.refactoring_health import parse_query as parse_refactoring_query

# The opportunity id space is shared with the performance pillar's, and told
# apart by prefix alone, so ``opportunity_id`` stays one selector.
_REFACTORING_OPPORTUNITY_PREFIX = "refop"

_REFACTORING_COLLECTION_CAP = 6
"""Opportunities per response, independent of ``limit``. ``cursor`` pages it."""

_REFACTORING_STEP_CAP = 3
"""Steps per row in the queue. The detail call pages the rest."""

_REFACTORING_STEP_PAGE_CAP = 20
"""Ceiling on one page of a detail call's ordered steps."""

_REFACTORING_EVIDENCE_CAP = 3
"""Evidence rows beside a detail response. ``only=['refactoring_evidence']`` pages more."""

_REFACTORING_EVIDENCE_PAGE_CAP = 20
"""Ceiling on one evidence page."""

_PERFORMANCE_COLLECTION_CAP = 6
"""Opportunities per response, independent of ``limit``. ``cursor`` pages it."""

_PERFORMANCE_EVIDENCE_CAP = 3
"""Evidence rows per opportunity in the collection. The detail call pages more."""

_PERFORMANCE_EVIDENCE_PAGE_CAP = 20
"""Ceiling on one evidence page. ``limit`` still means what it says below it,
including ``limit=0`` for the totals and no rows."""


@dataclass(frozen=True, slots=True)
class _PerformanceBlocks:
    """Everything the performance pillar contributes to one response."""

    page: PerformancePage | None = None
    summary: dict[str, Any] | None = None
    directive: dict[str, Any] | None = None
    ignored: dict[str, str] = field(default_factory=dict)


async def _performance_blocks(
    service: PerformanceHealthService,
    *,
    wants: Any,
    included: bool,
    file_paths: tuple[str, ...] | None,
    scoped: bool,
    limit: int,
    cursor: int,
    view: str | None,
    context: str | None,
    boundary: str | None,
    confidence: str | None,
    actionability: str | None,
    sort: str | None,
) -> _PerformanceBlocks:
    """Read the materialized queue, its rollup, and the dashboard lead.

    Each block is gated on surviving the projection, so a caller that asked for
    one of the three does not pay for the other two.
    """
    page = None
    query = None
    ignored: dict[str, str] = {}
    if included:
        # The lede quotes only the first row, so projected down to it, read one.
        emits_queue = wants("performance_opportunities")
        query, ignored = parse_query(
            context=context,
            boundary=boundary,
            confidence=confidence,
            actionability=actionability,
            view=view,
            sort=sort,
            file_paths=file_paths,
            limit=min(max(limit, 0), _PERFORMANCE_COLLECTION_CAP) if emits_queue else 1,
            offset=cursor if emits_queue else 0,
        )
        page = await service.page(
            query,
            evidence_per_item=_PERFORMANCE_EVIDENCE_CAP if emits_queue else 0,
            # Facets are rendered only by the summary block.
            with_facets=wants("performance_summary"),
        )
    return _PerformanceBlocks(
        page=page,
        summary=(
            # Same context as the queue, so the two totals cannot contradict.
            await service.summary(query.contexts if query else None)
            if included and wants("performance_summary")
            else None
        ),
        # Dashboard lead: one primary-key read, independent of repo size.
        directive=(
            await service.directive() if not scoped and wants("performance_directive") else None
        ),
        ignored=ignored,
    )


@dataclass(frozen=True, slots=True)
class _RefactoringBlocks:
    """Everything the refactoring pillar contributes to one response."""

    page: Any = None
    summary: dict[str, Any] | None = None
    directive: dict[str, Any] | None = None
    ignored: dict[str, str] = field(default_factory=dict)


async def _refactoring_blocks(
    service: RefactoringHealthService,
    *,
    wants: Any,
    included: bool,
    file_paths: tuple[str, ...] | None,
    scoped: bool,
    limit: int,
    cursor: int,
    view: str,
    lead_type: str | None = None,
    confidence: str | None = None,
    effort: str | None = None,
) -> _RefactoringBlocks:
    """Read the materialized queue, its rollup, and the dashboard lead.

    Each block is gated on surviving the projection, so a caller that asked for
    one of the three does not pay for the other two.
    """
    page = None
    ignored: dict[str, str] = {}
    # A scope that resolved to no file is not the dashboard: the rollup is read
    # by repository id and cannot honour a scope, so it is withheld.
    resolved_scope = not (scoped and not file_paths)
    rollup_wanted = included and wants("refactoring_summary") and resolved_scope
    if included:
        emits_queue = wants("refactoring_opportunities")
        query, ignored = parse_refactoring_query(
            view=view,
            lead_type=lead_type,
            confidence=confidence,
            effort=effort,
            file_paths=list(file_paths) if file_paths is not None else None,
            limit=min(max(limit, 0), _REFACTORING_COLLECTION_CAP) if emits_queue else 1,
            offset=cursor if emits_queue else 0,
        )
        page = await service.page(
            query,
            steps_per_item=_REFACTORING_STEP_CAP if emits_queue else 0,
            with_facets=rollup_wanted,
        )
    return _RefactoringBlocks(
        page=page,
        summary=await service.summary() if rollup_wanted else None,
        # Dashboard only: a targeted call is about the files the caller named.
        directive=(
            await service.directive()
            if not scoped and wants("refactoring_directive")
            else None
        ),
        ignored=ignored,
    )


def _merge_ignored(result: dict[str, Any], ignored: dict[str, str]) -> None:
    if ignored:
        result["ignored_arguments"] = {**result.get("ignored_arguments", {}), **ignored}


def _render_refactoring(
    result: dict[str, Any], refactoring: _RefactoringBlocks, req: HealthRequest, pager: Pager
) -> None:
    """The refactoring queue and its rollup, as read by ``_refactoring_blocks``."""
    page = refactoring.page
    if page is not None and req.wants("refactoring_opportunities"):
        result["refactoring_opportunities"] = page.items
        result["refactoring_opportunities_total"] = page.total
        result["refactoring_opportunities_emitted"] = len(page.items)
        if len(page.items) < page.total:
            result["refactoring_opportunities_reduced_reason"] = (
                "collection_cap" if req.limit > _REFACTORING_COLLECTION_CAP else "limit"
            )
        if page.next_offset is not None:
            pager.recoveries["refactoring_opportunities"] = (
                page.next_offset,
                _REFACTORING_COLLECTION_CAP,
                page.total - page.next_offset,
            )
        _merge_ignored(result, refactoring.ignored)

    if refactoring.summary is not None:
        result["refactoring_summary"] = {
            **refactoring.summary,
            "facets": page.facets if page else {},
            "view": req.refactoring_view,
            "next_call": (
                "get_health(include=['refactoring'], "
                "only=['refactoring_opportunities'], limit=6)"
            ),
        }


def _render_performance(
    result: dict[str, Any], performance: _PerformanceBlocks, req: HealthRequest, pager: Pager
) -> None:
    """The performance queue and its rollup, as read by ``_performance_blocks``."""
    page = performance.page
    if page is not None and req.wants("performance_opportunities"):
        result["performance_opportunities"] = page.items
        result["performance_opportunities_total"] = page.total
        result["performance_opportunities_emitted"] = len(page.items)
        if page.next_offset is not None:
            pager.recoveries["performance_opportunities"] = (
                page.next_offset,
                _PERFORMANCE_COLLECTION_CAP,
                page.total - page.next_offset,
            )
        _merge_ignored(result, performance.ignored)

    if performance.summary is not None:
        result["performance_summary"] = {
            **performance.summary,
            "facets": page.facets if page else {},
            "next_call": (
                "get_health(include=['performance'], "
                "only=['performance_opportunities'], limit=6)"
            ),
        }


_PERFORMANCE_LEAD_KEYS = (
    "opportunity_id",
    "intervention_symbol",
    "boundary_kind",
    "execution_context",
    "affected_call_sites_total",
    "rank_score",
)

_RECOMMENDATION_LEAD_KEYS = (
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


def _recommendation_lede(
    performance: _PerformanceBlocks,
    recommendations: list[Recommendation],
    reference_repository: str,
    req: HealthRequest,
) -> dict[str, Any]:
    """One performance opportunity beside one plan, when both pillars were asked for."""
    performance_lead = next(
        (
            item
            for item in (performance.page.items if performance.page else [])
            if item.get("biomarker_type") not in NON_LEADING_MARKERS
        ),
        None,
    )
    lead_payload = (
        _serialize_refactoring(recommendations[0], reference_repository)
        if recommendations
        else None
    )
    return {
        "performance_opportunities_total": (
            performance.page.total if performance.page else 0
        ),
        "refactoring_plans_total": len(recommendations),
        "performance_lead": (
            {key: performance_lead[key] for key in _PERFORMANCE_LEAD_KEYS}
            if performance_lead
            else None
        ),
        "recommendation_lead": (
            {key: lead_payload[key] for key in _RECOMMENDATION_LEAD_KEYS}
            if lead_payload
            else None
        ),
        # The lead's plan, from the one place that decides plan linkage.
        "performance_plan_id": (
            performance_lead["plan_reference"] if performance_lead else None
        ),
        "performance_plan_reason": (
            performance_lead["plan_reason"] if performance_lead else None
        ),
        "next_call": (
            f"get_health(targets={req.raw_targets!r}, repo={req.repo!r}, "
            "include=['performance','refactoring'], limit=3, "
            "only=['performance_opportunities','refactoring_plans'], "
            f"refactoring_view='{req.refactoring_view}')"
        ),
    }
