"""Bounded causal performance opportunities and raw-evidence drill-down.

A thin adapter: query parameters map onto the shared performance service, and
the response is what that service returned. Filtering, ordering, paging, plan
linkage, and facets live there, so this surface and the agent surface cannot
drift apart about what the same opportunity is.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.finding_registry import label_previews
from repowise.core.analysis.health.perf.actionability import MEASURED_HOTSPOT
from repowise.core.analysis.health.perf.serving import parse_query
from repowise.core.analysis.health.refactoring.serving import evidence_block
from repowise.core.ingestion.symbol_identity import file_of_symbol
from repowise.core.persistence.crud import get_performance_opportunity, hotness_for_symbols
from repowise.server.deps import get_db_session
from repowise.server.mcp_server.tool_context.measured import symbol_line
from repowise.server.services.performance_health import PerformanceHealthService

from .._unverified import UnverifiedQuery
from ._router import router


def _service(
    session: AsyncSession, repo_id: str, *, include_unverified: bool = False
) -> PerformanceHealthService:
    """Bind the shared service to this repository.

    The repository token only scopes the agent-address-space plan reference,
    which this surface strips: a plan here is addressed by the row id its own
    detail route resolves.
    """
    return PerformanceHealthService(
        session, repo_id, repo_id, include_unverified=include_unverified
    )


_EVIDENCE_PER_ITEM = 8
"""Evidence rows carried inline on a queue row, so the tab can show the paths."""


_MAX_SCOPED_PATHS = 50
"""Files one scoped request may name. A file surface asks about one or a few."""


def _paths(raw: str | None) -> tuple[str, ...] | None:
    """Split the file scope, or nothing when the caller did not scope."""
    if not raw:
        return None
    parts = tuple(dict.fromkeys(p.strip() for p in raw.split(",") if p.strip()))
    return parts[:_MAX_SCOPED_PATHS] or None


def _item(item: dict[str, Any]) -> dict[str, Any]:
    """Drop the reference minted for the other address space."""
    return {key: value for key, value in item.items() if key != "plan_reference"}


async def _measurement(
    session: AsyncSession, repo_id: str, biomarker_type: str | None, symbol_id: str | None
) -> list[dict[str, Any]]:
    """The evidence of a measured hotspot: the run that measured its function.

    No static finding backs such a cause, so its stored observations are
    empty. The run is the proof, read from the same ``symbol_hotness`` row the
    measured table shows; no row (the run was dropped) reads as no evidence.
    """
    if biomarker_type != MEASURED_HOTSPOT or not symbol_id:
        return []
    row = (await hotness_for_symbols(session, repo_id, [symbol_id])).get(symbol_id)
    if row is None:
        return []
    file_path = file_of_symbol(symbol_id)
    return [
        {
            # No static finding: the wire type is a string, so empty, never null.
            "finding_id": "",
            "file_path": file_path,
            "biomarker_type": MEASURED_HOTSPOT,
            "function_name": symbol_id[len(file_path) + 2 :] or None,
            "line_start": None,
            "line_end": None,
            "reason": f"Measured: {symbol_line(row, datetime.now(UTC))}",
            "path": [],
            "provenance": "measured",
        }
    ]


@router.get("/api/repos/{repo_id}/health/performance-opportunities")
async def list_performance_opportunities(
    repo_id: str,
    context: str | None = Query(
        None,
        description=(
            "Execution context to scope to: production, tooling, test, unknown "
            "or all. Defaults to production."
        ),
    ),
    boundary: str | None = Query(None),
    confidence: str | None = Query(None),
    actionability: str | None = Query(None),
    proof: str | None = Query(
        None,
        description=(
            "proven (the default) or unproven: causes whose loop size the code does "
            "not show, kept out of the default queue and counted in its summary."
        ),
    ),
    role: str | None = Query(
        None,
        description=(
            "Execution role: request, event_consumer, scheduled_job, startup, cli, "
            "tooling, test, unknown or all. Defaults to every role but startup, cli, "
            "tooling and test, whose loops run once per process."
        ),
    ),
    queue: bool = Query(
        False,
        description=(
            "Keep to the default queue's stored verdict whatever state is chosen, "
            "so a state picked inside 'worth doing now' stays inside it."
        ),
    ),
    area: str | None = Query(
        None, description="One area (a directory) at any depth; absent is the whole repository."
    ),
    area_depth: int | None = Query(
        None,
        ge=1,
        le=32,
        description="Group the area facet at this directory depth; absent chooses one.",
    ),
    view: str = Query("detail"),
    sort: str = Query("rank"),
    file_paths: str | None = Query(
        None,
        description=(
            "Comma-separated files to scope to, matched against the file "
            "holding each opportunity's intervention."
        ),
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    include_unverified: bool = UnverifiedQuery,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """One bounded page over the materialized causal read model.

    The rows fetched are the page, not the repository: grouping already
    happened when the findings were persisted.
    """
    query, ignored = parse_query(
        context=context,
        boundary=boundary,
        confidence=confidence,
        actionability=actionability,
        proof=proof,
        role=role,
        view=view,
        sort=sort,
        file_paths=_paths(file_paths),
        limit=limit,
        offset=offset,
        queue=queue,
        lift_defaults=True,
        area=area,
    )
    page = await _service(session, repo_id, include_unverified=include_unverified).page(
        query,
        evidence_per_item=_EVIDENCE_PER_ITEM,
        with_facets=True,
        with_summary=True,
        # The tab acts on summary rows, so they carry fix, gain and effort.
        row_decisions=True,
        # Every count the tab shows sits inside the scope it asked for.
        scoped_facets=True,
        area_depth=area_depth,
    )
    return {
        "items": label_previews(
            "performance", [_item(item) for item in page.items], kind="biomarker_type"
        ),
        "total": page.total,
        "has_more": page.next_offset is not None,
        "next_offset": page.next_offset,
        "facets": page.facets,
        "summary": page.summary,
        "counts": page.counts,
        "gated": page.gated,
        "scopes": page.scopes,
        "areas": page.areas,
        **({"ignored_arguments": ignored} if ignored else {}),
    }


@router.get("/api/repos/{repo_id}/health/performance-opportunities/{opportunity_id}")
async def get_performance_opportunity_detail(
    repo_id: str,
    opportunity_id: str,
    evidence_limit: int = Query(8, ge=0, le=200),
    evidence_offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """One opportunity by its stable id, with bounded evidence.

    An id from an older performance model resolves to an explicit stale-model
    state and the refresh that fixes it, rather than reading as "no plan".
    """
    detail = await _service(session, repo_id).detail(
        opportunity_id, evidence_limit=evidence_limit, evidence_offset=evidence_offset
    )
    if detail.get("found") and not detail.get("evidence_total"):
        measured = await _measurement(
            session, repo_id, detail.get("biomarker_type"), detail.get("intervention_symbol")
        )
        if measured:
            page = measured[evidence_offset : evidence_offset + evidence_limit]
            detail.update(evidence_block(page, len(measured), evidence_offset))
    return label_previews("performance", [_item(detail)], kind="biomarker_type")[0]


@router.get("/api/repos/{repo_id}/health/performance-opportunities/{opportunity_id}/findings")
async def list_performance_opportunity_findings(
    repo_id: str,
    opportunity_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Click-gated raw observations for one stable opportunity id."""
    items, total = await _service(session, repo_id).evidence(
        opportunity_id, limit=limit, offset=offset
    )
    if total == 0:
        row = await get_performance_opportunity(session, repo_id, opportunity_id)
        if row is not None:
            measured = await _measurement(
                session, repo_id, row.biomarker_type, row.intervention_symbol
            )
            items, total = measured[offset : offset + limit], len(measured)
    emitted = offset + len(items)
    return {
        "items": label_previews("performance", items, kind="biomarker_type"),
        "total": total,
        "has_more": emitted < total,
        "next_offset": emitted if emitted < total else None,
    }


__all__ = [
    "get_performance_opportunity_detail",
    "list_performance_opportunities",
    "list_performance_opportunity_findings",
]
