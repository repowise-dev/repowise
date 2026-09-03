"""Bounded causal performance opportunities and raw-evidence drill-down.

A thin adapter: query parameters map onto the shared performance service, and
the response is what that service returned. Filtering, ordering, paging, plan
linkage, and facets live there, so this surface and the agent surface cannot
drift apart about what the same opportunity is.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.server.deps import get_db_session
from repowise.server.services.performance_health import (
    PerformanceHealthService,
    parse_query,
)

from ._router import router


def _service(session: AsyncSession, repo_id: str) -> PerformanceHealthService:
    """Bind the shared service to this repository.

    The repository token only scopes the agent-address-space plan reference,
    which this surface strips: a plan here is addressed by the row id its own
    detail route resolves.
    """
    return PerformanceHealthService(session, repo_id, repo_id)


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
        view=view,
        sort=sort,
        file_paths=_paths(file_paths),
        limit=limit,
        offset=offset,
    )
    page = await _service(session, repo_id).page(
        query, evidence_per_item=_EVIDENCE_PER_ITEM, with_facets=True, with_summary=True
    )
    return {
        "items": [_item(item) for item in page.items],
        "total": page.total,
        "has_more": page.next_offset is not None,
        "next_offset": page.next_offset,
        "facets": page.facets,
        "summary": page.summary,
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
    return _item(detail)


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
    emitted = offset + len(items)
    return {
        "items": items,
        "total": total,
        "has_more": emitted < total,
        "next_offset": emitted if emitted < total else None,
    }


__all__ = [
    "get_performance_opportunity_detail",
    "list_performance_opportunities",
    "list_performance_opportunity_findings",
]
