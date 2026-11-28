"""Performance and refactoring pillar blocks for get_health: the queue and its rollup."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from repowise.core.analysis.health.perf.serving import parse_query
from repowise.core.analysis.health.refactoring.serving import parse_query as parse_refactoring_query
from repowise.core.analysis.next_call import ActionCommand
from repowise.core.evidence.action_row import row_compact, run_verify
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
    classes that may lead, ``in_tests`` from unit-test runs, ``statements``
    from production statement runs with the function that issues them,
    ``routes`` from production route runs with the handler that serves them,
    ``benchmarks`` from benchmark results with the function each measures) lists
    :data:`_MEASURED_CAP` rows per run, or ``limit`` up to
    :data:`_MEASURED_PAGE_CAP`, beside its total. A row an open static cause
    with production evidence sits on names it in ``on_static_findings``.
    Without ranked evidence it is one ``status`` token (``no_runtime_data``,
    ``stale``, ``not_ranked``): unknown, never zero.
    """
    from repowise.core.evidence.compare import comparison_line

    cap = min(req.limit, _MEASURED_PAGE_CAP) if req.limit_explicit else _MEASURED_CAP
    comparisons = await _comparisons(session, repository_id)
    # At least one row, so a group still reports its total with ``limit=0``.
    view = await measured_costs(session, repository_id, limit=max(cap, 1), comparisons=comparisons)
    # ``None`` means no workload's newest run ranks a function in a class that
    # leads or in ``unit_test``.
    slower = [comparison_line(c) for c in comparisons if c.verdict == "slower"]
    if view is None:
        return await _unranked_block(session, repository_id, slower)
    block: dict[str, Any] = {"basis": "measured", "runs": []}
    runs: dict[str, dict[str, Any]] = {}
    for group, key in (("measured", "rows"), ("measured_in_tests", "in_tests")):
        found = view.get(group)
        if not found:
            continue
        # Cut per run by the reader; ``limit=0`` keeps the totals and no rows.
        rows = found["rows"] if cap else []
        block[key] = [row_compact(row) for row in rows]
        # The nested stamping in ``paging`` adds ``_emitted`` and ``_reduced_reason``.
        block[f"{key}_total"] = found["total"]
        shown = {row["run_id"] for row in rows}
        for run in found["runs"]:
            if run["run_id"] in shown:
                runs.setdefault(run["run_id"], _measured_run(run))
    for key in ("statements", "routes"):
        found = view.get(key)
        if not found:
            continue
        rows = found["rows"] if cap else []
        if found["total"]:
            block[key] = [row_compact(row) for row in rows]
            block[f"{key}_total"] = found["total"]
        shown = {row["run_id"] for row in rows}
        named = {row["run_id"] for row in found["rows"]}
        for run in found["runs"]:
            # A statement window that joined no function still says where its
            # time went (a route run is only listed when it names a handler).
            if run["run_id"] in shown or run["run_id"] not in named:
                runs.setdefault(run["run_id"], _window_run(run))
    benchmarks = view.get("benchmarks")
    if benchmarks:
        rows = benchmarks["rows"] if cap else []
        block["benchmarks"] = [_benchmark_row(row) for row in rows]
        block["benchmarks_total"] = benchmarks["total"]
        # Each row's run, as for ranked rows: which workload it came from.
        shown = {row["run_id"] for row in rows}
        for run in benchmarks["runs"]:
            if run["run_id"] in shown:
                runs.setdefault(run["run_id"], _benchmark_run(run))
    if not any(key in block for key in ("rows", "in_tests", "statements", "routes", "benchmarks")):
        # Only windows that joined no function: nothing is ranked, so the
        # status token stays, beside the runs that say where the time went.
        return {
            **await _unranked_block(session, repository_id, slower),
            "runs": list(runs.values()),
        }
    block["runs"] = list(runs.values())
    if slower:
        block["slower"] = slower
    return block


async def _unranked_block(session: Any, repository_id: str, slower: list[str]) -> dict[str, Any]:
    """Why nothing is ranked, as one ``status`` token and the command that fixes it."""
    status = await unranked_reason(session, repository_id)
    block: dict[str, Any] = {"basis": "unknown", "status": status}
    command = unranked_command(status)
    if command:
        block["command"] = command
    if slower:
        block["slower"] = slower
    return block


def _benchmark_row(row: dict[str, Any]) -> dict[str, Any]:
    """``benchmark``, the function it ``measures`` and its numbers per op; unset ones left out."""
    keys = ("benchmark", "measures", "run_id", "time", "unit", "allocs_per_op")
    return {k: row[k] for k in keys if row.get(k) is not None}


def _benchmark_run(run: dict[str, Any]) -> dict[str, Any]:
    out = {f: run[f] for f in ("run_id", "workload_class", "workload_name", "commit", "age_days")}
    if out["commit"]:
        out["commit"] = out["commit"][:12]
    return out


def _window_run(run: dict[str, Any]) -> dict[str, Any]:
    """A production statement or route run: its basis, the next window as how
    its rows check a fix, and the share of its time on no single function, by
    what the join found (``ambiguous``, ``database_schedule``, ``unjoined``)."""
    out = _benchmark_run(run)
    verify = run_verify(run)
    if verify:
        out["verify"] = verify
    unattributed = {k: v["share"] for k, v in run["unattributed"].items() if v["share"]}
    if unattributed:
        out["unattributed"] = unattributed
    if run.get("window"):
        out["window"] = run["window"]["claim"]
    return out


async def _comparisons(session: Any, repository_id: str) -> list[Any]:
    """Each workload's newest run against its band (``evidence.compare``): a
    slower one is a ``slower`` line naming the commits that changed what it
    ran, and the rows it names say so. Empty when the store or git cannot say."""
    from repowise.core.evidence.compare_history import load_comparisons
    from repowise.core.persistence.models import Repository

    try:
        repository = await session.get(Repository, repository_id)
        # A savepoint, so a failed read leaves the request's transaction usable.
        async with session.begin_nested():
            found = await load_comparisons(
                session, repository_id, getattr(repository, "local_path", None) or None
            )
    except Exception:  # the measured block stands without it
        logging.getLogger(__name__).warning("Could not compare evidence runs.", exc_info=True)
        return []
    return list(found)


def _measured_run(run: dict[str, Any]) -> dict[str, Any]:
    out = {f: run[f] for f in _MEASURED_RUN_FIELDS}
    # How its rows check a fix, once per run: the workload rerun, or the next
    # window for production (a row names its own ``verify`` only when a
    # benchmark measures it).
    verify = run_verify(run)
    if verify:
        out["verify"] = verify
    # Twelve characters name a commit for git; the full SHA is the CLI's JSON.
    if out["commit"]:
        out["commit"] = out["commit"][:12]
    # Only a tracer's run says so: every other run gives shares.
    if not out["ordering_only"]:
        del out["ordering_only"]
    # Only a production run names the window and deploy it measured, as its rows' claim.
    window = run.get("window")
    if window:
        out["window"] = window["claim"]
    # Only a run listed in place of a newer one that ranks nothing says so.
    newer = run.get("newer_run")
    if newer:
        commit = newer["commit"]
        out["newer_run"] = {"commit": commit[:12] if commit else None, "reason": newer["reason"]}
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
