"""Performance and refactoring pillar blocks for get_health: the queue and its rollup."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from repowise.core.analysis.health.perf.serving import parse_query
from repowise.core.analysis.health.refactoring.serving import parse_query as parse_refactoring_query
from repowise.core.analysis.next_call import ActionCommand
from repowise.core.persistence.crud import measured_costs, unranked_command, unranked_reason
from repowise.server.mcp_server.tool_health.paging import Pager
from repowise.server.mcp_server.tool_health.request import HealthRequest
from repowise.server.services.performance_health import (
    PerformanceHealthService,
    PerformancePage,
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

_MEASURED_CAP = 3
"""Measured functions per run when no ``limit`` is passed."""

_MEASURED_PAGE_CAP = 10
"""Ceiling on measured functions per run: the reader ranks at most ten per run."""

#: Run fields a measured block carries: its basis, age, sample size and where
#: its time went (production repo code itself, waits, libraries).
_MEASURED_RUN_FIELDS = (
    "run_id",
    "workload_class",
    "workload_name",
    "commit",
    "age_days",
    "samples",
    "repo_self_share",
    "wait_share",
    "library_share",
    "outside_production",
    "value_kind",
    "ordering_only",
)

#: Row fields: ``share`` is self time (the cost ranked by), ``inclusive``
#: context. Callers, owner and tests are one ``get_context`` call away.
_MEASURED_ROW_FIELDS = (
    "symbol_id",
    "lines",
    "run_id",
    "rank",
    "share",
    "share_low",
    "share_high",
    "inclusive",
    "samples",
    "freshness",
    "callers_total",
)


@dataclass(frozen=True, slots=True)
class _PerformanceBlocks:
    """Everything the performance pillar contributes to one response."""

    page: PerformancePage | None = None
    summary: dict[str, Any] | None = None
    ignored: dict[str, str] = field(default_factory=dict)
    #: ``performance_measured``: what stored runs measured, or why nothing is.
    measured: dict[str, Any] | None = None


async def _measured_block(session: Any, repository_id: str, req: HealthRequest) -> dict[str, Any]:
    """The ``performance_measured`` block: ranked functions with their runs.

    One run per workload, its newest. Each group (``rows`` from workload
    classes that may lead, ``in_tests`` from unit-test runs) lists
    :data:`_MEASURED_CAP` rows per run, or ``limit`` up to
    :data:`_MEASURED_PAGE_CAP`, beside its total. A row an open static cause
    with production evidence sits on names it in ``on_static_findings``.
    Without ranked evidence it is one ``status`` token (``no_runtime_data``,
    ``stale``, ``not_ranked``): unknown, never zero.
    """
    cap = min(req.limit, _MEASURED_PAGE_CAP) if req.limit_explicit else _MEASURED_CAP
    # At least one row, so a group still reports its total with ``limit=0``.
    view = await measured_costs(session, repository_id, limit=max(cap, 1))
    # ``None`` means no workload's newest run ranks a function in a class that
    # leads or in ``unit_test``.
    if view is None:
        status = await unranked_reason(session, repository_id)
        block: dict[str, Any] = {"basis": "unknown", "status": status}
        command = unranked_command(status)
        if command:
            block["command"] = command
        return block
    block = {"basis": "measured", "runs": []}
    runs: dict[str, dict[str, Any]] = {}
    for group, key in (("measured", "rows"), ("measured_in_tests", "in_tests")):
        found = view.get(group)
        if not found:
            continue
        # Cut per run by the reader; ``limit=0`` keeps the totals and no rows.
        rows = found["rows"] if cap else []
        block[key] = [_measured_row(row) for row in rows]
        # The nested stamping in ``paging`` adds ``_emitted`` and ``_reduced_reason``.
        block[f"{key}_total"] = found["total"]
        shown = {row["run_id"] for row in rows}
        for run in found["runs"]:
            if run["run_id"] in shown:
                runs.setdefault(run["run_id"], _measured_run(run))
    block["runs"] = list(runs.values())
    return block


def _measured_run(run: dict[str, Any]) -> dict[str, Any]:
    out = {f: run[f] for f in _MEASURED_RUN_FIELDS}
    # Twelve characters name a commit for git; the full SHA is the CLI's JSON.
    if out["commit"]:
        out["commit"] = out["commit"][:12]
    # Only a tracer's run says so: every other run gives shares.
    if not out["ordering_only"]:
        del out["ordering_only"]
    return out


def _measured_row(row: dict[str, Any]) -> dict[str, Any]:
    out = {f: row[f] for f in _MEASURED_ROW_FIELDS}
    # ``"start-end"``: a list here would be stamped with counts like a collection.
    lines = row["lines"]
    out["lines"] = f"{lines[0]}-{lines[1]}" if lines else None
    if row.get("on_static_findings"):
        # Open with ``get_health(opportunity_id=...)``.
        out["on_static_findings"] = row["on_static_findings"]
    return out


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
        result["performance_opportunities_counts"] = page.counts
        if page.next_offset is not None:
            pager.recoveries["performance_opportunities"] = (
                page.next_offset,
                _PERFORMANCE_COLLECTION_CAP,
                page.total - page.next_offset,
            )
        _merge_ignored(result, performance.ignored)

    if performance.summary is not None:
        summary = {**performance.summary, "facets": page.facets if page else {}}
        # Its call returns the queue: dropped only when this response lists items.
        if not result.get("performance_opportunities"):
            summary["next_call"] = ActionCommand.call(
                "The ranked performance opportunities, lead first",
                "get_health",
                {"include": ["performance"], "only": ["performance_opportunities"], "limit": 6},
            ).as_dict()
        result["performance_summary"] = summary

    if performance.measured is not None:
        result["performance_measured"] = performance.measured
