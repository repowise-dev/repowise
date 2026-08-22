"""Bounded causal performance opportunities and raw-evidence drill-down."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.perf.opportunities import (
    build_performance_opportunities,
    opportunity_id_for_finding,
)
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session

from ._router import router
from .loaders import _attach_symbol_ids
from .serializers import _finding_to_dict


def _json_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _plan_ids(rows: list[Any]) -> dict[str, str]:
    matches: dict[str, str] = {}
    for row in rows:
        opportunity_id = _json_dict(row.plan_json).get("opportunity_id")
        if isinstance(opportunity_id, str) and opportunity_id:
            plan_id = str(row.id)
            matches[opportunity_id] = min(matches.get(opportunity_id, plan_id), plan_id)
    return matches


def _finding_opportunity_id(row: Any) -> str:
    persisted = _json_dict(getattr(row, "details_json", None)).get("opportunity_id")
    return (
        persisted if isinstance(persisted, str) and persisted else opportunity_id_for_finding(row)
    )


def _plan_link(opportunity: Any, plan_id: str | None) -> dict[str, str | None]:
    if plan_id:
        return {
            "plan_id": plan_id,
            "plan_status": "available",
            "plan_reason": "A stored performance plan addresses this exact opportunity.",
        }
    if opportunity.fix is None:
        return {
            "plan_id": None,
            "plan_status": "no_safe_plan",
            "plan_reason": (
                "The analysis found the shared cause but could not prove one coherent "
                "intervention without guessing."
            ),
        }
    return {
        "plan_id": None,
        "plan_status": "not_persisted",
        "plan_reason": (
            "A supported strategy exists, but this index does not contain its matching "
            "stored plan. Reindex to refresh recommendations."
        ),
    }


@router.get("/api/repos/{repo_id}/health/performance-opportunities")
async def list_performance_opportunities(
    repo_id: str,
    context: Literal["production_tooling", "test", "all"] = Query("production_tooling"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """One bounded page over the canonical causal read model.

    Findings and performance-plan rows are each read once. Exact
    ``opportunity_id`` equality is the only accepted plan match.
    """
    findings = await crud.get_health_findings(session, repo_id, dimension="performance")
    plan_rows = await crud.get_refactoring_suggestions(
        session, repo_id, refactoring_type="performance_fix"
    )
    opportunities = build_performance_opportunities(findings, evidence_limit=8)
    matches = _plan_ids(plan_rows)
    contexts = {
        "production_tooling": {"production", "tooling"},
        "test": {"test"},
        "all": {"production", "tooling", "test"},
    }[context]
    filtered = [item for item in opportunities if item.execution_context in contexts]
    total = len(filtered)
    page = filtered[offset : offset + limit]
    next_offset = offset + len(page) if offset + len(page) < total else None
    return {
        "items": [
            {
                **item.as_dict(),
                **_plan_link(item, matches.get(item.opportunity_id)),
            }
            for item in page
        ],
        "total": total,
        "has_more": next_offset is not None,
        "next_offset": next_offset,
        "summary": {
            "total": len(opportunities),
            "production_total": sum(
                item.execution_context == "production" for item in opportunities
            ),
            "tooling_total": sum(item.execution_context == "tooling" for item in opportunities),
            "test_total": sum(item.execution_context == "test" for item in opportunities),
            "with_plan_total": sum(item.opportunity_id in matches for item in opportunities),
            "without_plan_total": sum(item.opportunity_id not in matches for item in opportunities),
        },
    }


@router.get("/api/repos/{repo_id}/health/performance-opportunities/{opportunity_id}/findings")
async def list_performance_opportunity_findings(
    repo_id: str,
    opportunity_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Click-gated raw canonical observations for one stable opportunity id."""
    findings = await crud.get_health_findings(session, repo_id, dimension="performance")
    matched = [
        finding for finding in findings if _finding_opportunity_id(finding) == opportunity_id
    ]
    matched.sort(key=lambda row: (row.file_path, row.line_start or 0, row.id))
    total = len(matched)
    page = matched[offset : offset + limit]
    items = await _attach_symbol_ids(
        session, repo_id, [_finding_to_dict(finding) for finding in page]
    )
    next_offset = offset + len(page) if offset + len(page) < total else None
    return {
        "items": items,
        "total": total,
        "has_more": next_offset is not None,
        "next_offset": next_offset,
    }


__all__ = ["list_performance_opportunities", "list_performance_opportunity_findings"]
