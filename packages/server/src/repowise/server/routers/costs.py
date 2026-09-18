"""/api/repos/{repo_id}/costs — LLM cost tracking endpoints."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.core.persistence.models import LlmCost
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import (
    CostGroupResponse,
    CostSummaryResponse,
    SavingsAgentRow,
    SavingsBreakdownRow,
    SavingsOpportunityRow,
    SavingsResponse,
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
    session: AsyncSession = Depends(get_db_session),
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
    session: AsyncSession = Depends(get_db_session),
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


@router.get("/{repo_id}/savings", response_model=SavingsResponse)
async def get_savings(
    repo_id: str,
    # Bounded, not just non-negative: a large enough value overflows the
    # timedelta the report subtracts, and a decade already means "all time"
    # for a ledger this young.
    days: int | None = Query(
        None, ge=0, le=3_650, description="Window in days; omit for all time"
    ),
    session: AsyncSession = Depends(get_db_session),
) -> SavingsResponse:
    """What agents avoided in this repository, from the canonical ledger.

    Reads through the one core report service, so this endpoint, the
    repository overview headline and ``repowise saved`` report the same
    numbers by construction rather than by three implementations agreeing.

    The transcript miners below are *observed opportunities* -- things that
    could have been saved and were not. They are reported beside the achieved
    savings and never added to them.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    if not repo.local_path:
        return SavingsResponse(available=False)

    from repowise.core.savings.service import load_report

    as_of = datetime.now(UTC)
    report = load_report(repo.local_path, as_of=as_of, days=days)
    if report is None:
        return SavingsResponse(available=False)

    # Best-effort transcript scans; both degrade to an empty report and never
    # raise. Kept out of the ledger entirely -- they are estimates of what was
    # not saved, and the contract says those never enter the headline.
    from repowise.core.distill.missed import scan_missed_savings
    from repowise.core.distill.missed_mcp import scan_missed_mcp_savings

    missed = scan_missed_savings(Path(repo.local_path))
    reread = scan_missed_mcp_savings(Path(repo.local_path))

    return SavingsResponse(
        available=True,
        window_days=days,
        as_of=as_of.isoformat(),
        first_event_at=report.first_event_at,
        last_event_at=report.last_event_at,
        unique_events=report.unique_events,
        successful_or_usable_partial_events=report.successful_or_usable_partial_events,
        saving_interactions=report.saving_interactions,
        mcp_queries_answered=report.mcp_queries_answered,
        dead_ends=report.dead_ends,
        saved_input_tokens=report.saved_input_tokens,
        measured_saved_input_tokens=report.measured_saved_input_tokens,
        inferred_saved_input_tokens=report.inferred_saved_input_tokens,
        priced_saved_input_tokens=report.priced_saved_input_tokens,
        unpriced_saved_input_tokens=report.unpriced_saved_input_tokens,
        priced_input_savings_usd=report.priced_input_savings_usd,
        saved_output_tokens=report.saved_output_tokens,
        priced_saved_output_tokens=report.priced_saved_output_tokens,
        unpriced_saved_output_tokens=report.unpriced_saved_output_tokens,
        priced_output_savings_usd=report.priced_output_savings_usd,
        per_operation=_breakdown(report.per_operation, "operation"),
        per_surface=_breakdown(report.per_surface, "surface"),
        per_agent=[SavingsAgentRow(**row) for row in report.per_agent],
        per_model=_breakdown(report.per_model, "model"),
        per_day=_breakdown(report.per_day, "day"),
        opportunity_count=report.opportunity_count,
        opportunity_tokens_excluded=report.opportunity_tokens_excluded,
        per_opportunity_kind=[
            SavingsOpportunityRow(**row) for row in report.per_opportunity_kind
        ],
        missed_events=missed["events"],
        missed_tokens_est=missed["est_saved_tokens"],
        missed_window_days=missed["window_days"],
        reread_events=reread["events"],
        reread_tokens_est=reread["est_saved_tokens"],
    )


def _breakdown(rows: Iterable[Mapping[str, Any]], key: str) -> list[SavingsBreakdownRow]:
    """Map one core breakdown onto the wire row, renaming its group column.

    The core report names each bucket after what it groups by; the wire uses
    one row shape so a client needs one renderer rather than five.
    """
    return [
        SavingsBreakdownRow(
            group=None if row[key] is None else str(row[key]),
            events=row["events"],
            saved_input_tokens=row["saved_input_tokens"],
        )
        for row in rows
    ]
