"""/api/repos/{repo_id}/costs — LLM cost tracking endpoints."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.core.persistence.models import LlmCost
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import (
    CostGroupResponse,
    CostSummaryResponse,
    DistillSavingsGroup,
    DistillSavingsResponse,
)

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


#: Pricing model for the savings dollar estimate — saved tokens are
#: input-side tokens the coding agent never had to read.
_SAVINGS_PRICING_MODEL = "claude-sonnet-4-6"


@router.get("/{repo_id}/distill-savings", response_model=DistillSavingsResponse)
async def get_distill_savings(
    repo_id: str,
    since: str | None = Query(None, description="ISO date filter, e.g. 2025-01-01"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> DistillSavingsResponse:
    """Savings-ledger rollup from the repo's omission store sidecar.

    Covers the ``repowise distill`` command/hook path only; MCP response
    truncation is not recorded in the ledger. Returns ``available=False``
    when the repo has no omission store on disk.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    if not repo.local_path:
        return DistillSavingsResponse(available=False)

    db_path = Path(repo.local_path) / ".repowise" / "omissions" / "omissions.db"
    if not db_path.is_file():
        return DistillSavingsResponse(available=False)

    since_dt = _parse_since(since)
    since_ts = since_dt.timestamp() if since_dt is not None else None

    from repowise.core.distill import tracking
    from repowise.core.generation.cost_tracker import get_model_pricing

    # Read-only stdlib sqlite3 on the sidecar: tiny aggregate queries, and a
    # ro handle never contends with hook/CLI writers.
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return DistillSavingsResponse(available=False)
    try:
        summary = tracking.savings_summary(conn, since=since_ts)
        per_filter = tracking.savings_rollup(conn, by="filter", since=since_ts)
        per_day = tracking.savings_rollup(conn, by="day", since=since_ts)
    except sqlite3.Error:
        return DistillSavingsResponse(available=False)
    finally:
        conn.close()

    rate = get_model_pricing(_SAVINGS_PRICING_MODEL)["input"]
    return DistillSavingsResponse(
        available=True,
        events=summary["events"],
        raw_tokens=summary["raw_tokens"],
        distilled_tokens=summary["distilled_tokens"],
        saved_tokens=summary["saved_tokens"],
        estimated_usd_saved=summary["saved_tokens"] * rate / 1_000_000,
        pricing_model=_SAVINGS_PRICING_MODEL,
        per_filter=[DistillSavingsGroup(**row) for row in per_filter],
        per_day=[DistillSavingsGroup(**row) for row in per_day],
    )
