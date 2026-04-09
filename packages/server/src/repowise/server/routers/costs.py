"""/api/repos/{repo_id}/costs — LLM cost tracking endpoints."""

from __future__ import annotations

from datetime import datetime, date

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, Query
from repowise.core.persistence.models import LlmCost
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import CostGroupResponse, CostSummaryResponse

router = APIRouter(
    prefix="/api/repos",
    tags=["costs"],
    dependencies=[Depends(verify_api_key)],
)


def _parse_since(since: str | None) -> datetime | None:
    """Parse an ISO date string (YYYY-MM-DD) into a datetime, or return None."""
    if since is None:
        return None
    try:
        return datetime.fromisoformat(since)
    except ValueError:
        # Try date-only format
        return datetime.combine(date.fromisoformat(since), datetime.min.time())


@router.get("/{repo_id}/costs/summary", response_model=CostSummaryResponse)
async def get_cost_summary(
    repo_id: str,
    since: str | None = Query(None, description="ISO date filter, e.g. 2025-01-01"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CostSummaryResponse:
    """Return aggregate cost totals for a repository."""
    since_dt = _parse_since(since)

    stmt = sa.select(
        sa.func.count().label("calls"),
        sa.func.sum(LlmCost.input_tokens).label("input_tokens"),
        sa.func.sum(LlmCost.output_tokens).label("output_tokens"),
        sa.func.sum(LlmCost.cost_usd).label("cost_usd"),
    ).where(LlmCost.repository_id == repo_id)

    if since_dt is not None:
        stmt = stmt.where(LlmCost.ts >= since_dt)

    result = await session.execute(stmt)
    row = result.one()

    return CostSummaryResponse(
        total_cost_usd=row.cost_usd or 0.0,
        total_calls=row.calls or 0,
        total_input_tokens=row.input_tokens or 0,
        total_output_tokens=row.output_tokens or 0,
        since=since,
    )


@router.get("/{repo_id}/costs", response_model=list[CostGroupResponse])
async def list_costs(
    repo_id: str,
    since: str | None = Query(None, description="ISO date filter, e.g. 2025-01-01"),
    by: str = Query("day", description="Grouping dimension: operation | model | day"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[CostGroupResponse]:
    """Return grouped cost totals for a repository."""
    since_dt = _parse_since(since)

    if by == "model":
        group_col = LlmCost.model
    elif by == "day":
        group_col = sa.func.strftime("%Y-%m-%d", LlmCost.ts)
    else:
        # Default: operation
        group_col = LlmCost.operation

    stmt = (
        sa.select(
            group_col.label("group"),
            sa.func.count().label("calls"),
            sa.func.sum(LlmCost.input_tokens).label("input_tokens"),
            sa.func.sum(LlmCost.output_tokens).label("output_tokens"),
            sa.func.sum(LlmCost.cost_usd).label("cost_usd"),
        )
        .where(LlmCost.repository_id == repo_id)
        .group_by(group_col)
        .order_by(sa.func.sum(LlmCost.cost_usd).desc())
    )

    if since_dt is not None:
        stmt = stmt.where(LlmCost.ts >= since_dt)

    result = await session.execute(stmt)
    rows = result.fetchall()

    return [
        CostGroupResponse(
            group=row.group or "(unknown)",
            calls=row.calls or 0,
            input_tokens=row.input_tokens or 0,
            output_tokens=row.output_tokens or 0,
            cost_usd=row.cost_usd or 0.0,
        )
        for row in rows
    ]
