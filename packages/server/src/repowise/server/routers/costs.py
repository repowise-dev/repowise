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
    McpDropGroup,
)

router = APIRouter(
    prefix="/api/repos",
    tags=["costs"],
    dependencies=[Depends(verify_api_key)],
)


#: Output tokens credited per answered counterfactual MCP query. Each curated
#: answer stands in for at least one raw exploration tool call the agent never
#: had to emit (README markets "-70% tool calls"), and that invocation is output
#: tokens we otherwise ignore. Deliberately one avoided call's worth (~a tool_use
#: block), not the several reads a single answer often replaces, so the credit
#: stays an undersell; priced at the agent's output rate below.
_AVOIDED_CALL_OUTPUT_TOKENS = 60


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


@router.get("/{repo_id}/distill-savings", response_model=DistillSavingsResponse)
async def get_distill_savings(
    repo_id: str,
    since: str | None = Query(None, description="ISO date filter, e.g. 2025-01-01"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> DistillSavingsResponse:
    """Savings rollup from the repo's omission store sidecar.

    Combines the ``repowise distill`` ledger with the MCP truncation drops
    already on disk (``source='mcp:*'``), priced at the coding agent's detected
    model. Returns ``available=False`` when the repo has no omission store.
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
    from repowise.core.distill.session_model import resolve_session_model
    from repowise.core.generation.cost_tracker import get_model_pricing

    # Read-only stdlib sqlite3 on the sidecar: tiny aggregate queries, and a
    # ro handle never contends with hook/CLI writers.
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return DistillSavingsResponse(available=False)
    try:
        # Distill figures exclude the ``mcp:*`` counterfactual rows Phase 2 now
        # writes into the same ledger; the MCP block reports those separately.
        summary = tracking.distill_summary(conn, since=since_ts)
        per_day = tracking.savings_rollup(conn, by="day", since=since_ts)
        # Unified MCP view: counterfactual ledger merged with truncation drops,
        # counterfactual taking precedence per tool (no double counting).
        mcp = tracking.mcp_savings_summary(conn, since=since_ts)
    except sqlite3.Error:
        return DistillSavingsResponse(available=False)
    finally:
        conn.close()

    per_filter = [{"group": name, **stats} for name, stats in summary["per_filter"].items()]

    # Missed savings: best-effort scan of local agent transcripts; the module
    # degrades to an empty report on any failure, never raises.
    from repowise.core.distill.missed import scan_missed_savings
    from repowise.core.distill.missed_mcp import scan_missed_mcp_savings

    missed = scan_missed_savings(Path(repo.local_path))
    reread = scan_missed_mcp_savings(Path(repo.local_path))

    # Price at the coding agent's actual model — saved tokens are input tokens
    # that agent never had to read. Detection is best-effort (sonnet default).
    resolved = resolve_session_model(Path(repo.local_path))
    pricing = get_model_pricing(resolved.model)
    total_saved = summary["saved_tokens"] + mcp["tokens"]
    input_usd = total_saved * pricing["input"] / 1_000_000
    # Add a small credit for the tool-call output the agent never had to emit:
    # every answered counterfactual MCP query replaced at least one raw
    # exploration call. Priced at the output rate; see _AVOIDED_CALL_OUTPUT_TOKENS.
    # Counted over net-positive counterfactual rows only — a dead-end (error)
    # call is recorded as a debit and saved the agent nothing, so crediting its
    # output would flip that debit into a credit.
    avoided_calls = sum(
        row["events"]
        for row in mcp["per_tool"]
        if row.get("kind") == "counterfactual" and row.get("tokens", 0) > 0
    )
    output_usd = avoided_calls * _AVOIDED_CALL_OUTPUT_TOKENS * pricing["output"] / 1_000_000
    return DistillSavingsResponse(
        available=True,
        events=summary["events"],
        raw_tokens=summary["raw_tokens"],
        distilled_tokens=summary["distilled_tokens"],
        saved_tokens=summary["saved_tokens"],
        estimated_usd_saved=input_usd + output_usd,
        pricing_model=resolved.model,
        pricing_agent=resolved.agent,
        pricing_source=resolved.source,
        per_filter=[DistillSavingsGroup(**row) for row in per_filter],
        per_day=[DistillSavingsGroup(**row) for row in per_day],
        mcp_events=mcp["events"],
        mcp_tokens=mcp["tokens"],
        mcp_queries=mcp["queries"],
        mcp_per_tool=[
            McpDropGroup(
                tool=row["tool"],
                events=row["events"],
                tokens=row["tokens"],
                kind=row["kind"],
            )
            for row in mcp["per_tool"]
        ],
        missed_events=missed["events"],
        missed_tokens_est=missed["est_saved_tokens"],
        missed_window_days=missed["window_days"],
        reread_events=reread["events"],
        reread_tokens_est=reread["est_saved_tokens"],
    )
