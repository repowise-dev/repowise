"""Performance and refactoring pillar blocks for get_health: the queue and its rollup."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from repowise.core.analysis.health.refactoring.serving import parse_query as parse_refactoring_query
from repowise.core.analysis.next_call import ActionCommand
from repowise.server.mcp_server.tool_health.paging import Pager
from repowise.server.mcp_server.tool_health.request import HealthRequest
from repowise.server.services.performance_health import (
    PerformanceHealthService,
    PerformancePage,
    parse_query,
)
from repowise.server.services.refactoring_health import RefactoringHealthService

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
    ignored: dict[str, str] = field(default_factory=dict)


async def _performance_blocks(
    service: PerformanceHealthService,
    *,
    wants: Any,
    included: bool,
    file_paths: tuple[str, ...] | None,
    limit: int,
    cursor: int,
    view: str | None,
    context: str | None,
    boundary: str | None,
    confidence: str | None,
    actionability: str | None,
    sort: str | None,
) -> _PerformanceBlocks:
    """Read the materialized queue and its rollup.

    Each block is gated on surviving the projection, so a caller that asked for
    one does not pay for the other.
    """
    page = None
    query = None
    ignored: dict[str, str] = {}
    if included:
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
        ignored=ignored,
    )


@dataclass(frozen=True, slots=True)
class _RefactoringBlocks:
    """Everything the refactoring pillar contributes to one response."""

    page: Any = None
    summary: dict[str, Any] | None = None
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
    scope: str | None = None,
) -> _RefactoringBlocks:
    """Read the materialized queue and its rollup.

    Each block is gated on surviving the projection, so a caller that asked for
    one does not pay for the other.
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
            scope=scope,
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
        result["refactoring_opportunities_scope"] = page.scope
        if page.hidden is not None:
            # What the default scope leaves out, by reason; ``all`` lists it.
            result["refactoring_opportunities_hidden"] = page.hidden
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
            "next_call": ActionCommand.call(
                "The ranked refactoring opportunities, lead first",
                "get_health",
                {"include": ["refactoring"], "only": ["refactoring_opportunities"], "limit": 6},
            ).as_dict(),
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
            "next_call": ActionCommand.call(
                "The ranked performance opportunities, lead first",
                "get_health",
                {"include": ["performance"], "only": ["performance_opportunities"], "limit": 6},
            ).as_dict(),
        }
