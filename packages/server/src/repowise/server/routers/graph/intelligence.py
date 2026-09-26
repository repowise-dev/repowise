"""Symbol-level graph intelligence: metrics, callers/callees, execution flows."""

from __future__ import annotations

from itertools import pairwise
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.ingestion.models import (
    FILE_DEPENDENCY_EDGE_TYPES,
    SYMBOL_USE_EDGE_TYPES,
)
from repowise.core.persistence import crud
from repowise.core.persistence.models import GraphNode
from repowise.server.deps import get_db_session
from repowise.server.mcp_server._graph_utils import (
    bfs_trace,
    parse_community_meta,
    percentile_rank,
    resolve_trace_communities,
)
from repowise.server.mcp_server._graph_utils import (
    entry_point_score as _ep_score,
)
from repowise.server.routers.graph._common import with_repo
from repowise.server.schemas import (
    CallersCalleesResponse,
    ExecutionFlowEntry,
    ExecutionFlowsResponse,
    GraphMetricsResponse,
    SymbolNodeSummary,
)
from repowise.server.schemas.intelligence import SymbolRelationGroup
from repowise.server.services.symbol_relations import load_symbol_relations

router = APIRouter()


@router.get("/{repo_id}/metrics", response_model=GraphMetricsResponse)
async def get_graph_metrics(
    repo_id: str,
    node_id: str = Query(..., description="File path or symbol_id"),
    session: AsyncSession = Depends(get_db_session),
    _repo: object = Depends(with_repo),
) -> GraphMetricsResponse:
    """Return importance metrics for a file or symbol with percentile ranks."""
    node = await crud.get_graph_node(session, repo_id, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

    pagerank = node.pagerank or 0.0
    betweenness = node.betweenness or 0.0
    kind, file = (node.kind, node.file_path) if node.node_type == "symbol" else (None, None)
    pagerank_percentile, betweenness_percentile = await _file_percentiles(
        session, repo_id, pagerank, betweenness
    )

    degrees = await _layer_degrees(session, repo_id, node)
    meta = parse_community_meta(node)

    return GraphMetricsResponse(
        target=node_id,
        node_type=node.node_type or "file",
        pagerank=round(pagerank, 6),
        pagerank_percentile=pagerank_percentile,
        betweenness=round(betweenness, 6),
        betweenness_percentile=betweenness_percentile,
        betweenness_scored=node.betweenness_commit is not None,
        community_id=node.community_id or 0,
        community_label=meta.get("label") or None,
        is_entry_point=node.is_entry_point,
        in_degree=degrees["in_degree"],
        out_degree=degrees["out_degree"],
        entry_point_score=meta.get("entry_point_score"),
        kind=kind,
        file=file,
    )


async def _layer_degrees(session: AsyncSession, repo_id: str, node: GraphNode) -> dict[str, int]:
    """In/out degree over the node's own layer: symbol-use or file-dependency edges.

    This endpoint feeds the same symbol component as /api/symbols/detail (the
    drawer, where that page is the drill-down), so an unscoped count here made
    one symbol report two different degrees depending on which the user opened.
    """
    is_symbol = node.node_type == "symbol"
    return await crud.get_node_degree_counts(
        session,
        repo_id,
        node.node_id,
        edge_types=sorted(SYMBOL_USE_EDGE_TYPES if is_symbol else FILE_DEPENDENCY_EDGE_TYPES),
    )


async def _file_percentiles(
    session: AsyncSession, repo_id: str, pagerank: float, betweenness: float
) -> tuple[int, int]:
    """Rank a pagerank and betweenness against every file node in the repo."""
    all_files = await crud.get_all_file_metrics(session, repo_id)
    return (
        percentile_rank(pagerank, [n.pagerank or 0.0 for n in all_files]),
        percentile_rank(betweenness, [n.betweenness or 0.0 for n in all_files]),
    )


@router.get("/{repo_id}/callers-callees", response_model=CallersCalleesResponse)
async def get_callers_callees(
    repo_id: str,
    symbol_id: str = Query(..., description="Symbol node ID (path::Name)"),
    direction: str = Query("both", description="callers, callees, or both"),
    edge_types: str = Query(
        "", description="Optional comma-separated filter on `relations` kinds; empty means all"
    ),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    _repo: object = Depends(with_repo),
) -> CallersCalleesResponse:
    """Who calls a symbol, what it calls, and every other relation it has.

    Shares `load_symbol_relations` with `/api/symbols/detail`, so the drawer
    this feeds and the routed symbol page cannot disagree about what reaches a
    symbol. Two things changed when they were joined:

    - `callers`/`callees` are `calls` edges only. They used to be whatever
      `edge_types` asked for, under one heading, so a subclass could be served
      as a caller. Heritage and framework wiring are in `relations`, named.
    - `caller_count`/`callee_count` are the true totals, not the number of
      rows served. They were `len(callers)`, capped at `limit`, and
      `symbol-drawer-wrapper.tsx` renders them as `caller_total` — so a symbol
      with 275 callers reported 20.

    `edge_types` now filters `relations` rather than deciding what counts as a
    caller. No client in this repo passes it; both `useCallersCallees` call
    sites omit it.
    """
    if direction not in ("callers", "callees", "both"):
        direction = "both"

    et_filter = {t.strip() for t in edge_types.split(",") if t.strip()}

    node = await _resolve_symbol(session, repo_id, symbol_id)
    relations = await load_symbol_relations(
        session, repo_id, node.node_id, present=True, call_row_cap=limit
    )
    groups = [g for g in relations.groups if _wants_group(g, et_filter, direction)]

    callers, caller_count = (
        (relations.callers, relations.caller_total) if direction != "callees" else ([], 0)
    )
    callees, callee_count = (
        (relations.callees, relations.callee_total) if direction != "callers" else ([], 0)
    )
    return CallersCalleesResponse(
        symbol_id=node.node_id,
        symbol=_symbol_summary(node),
        callers=callers,
        callees=callees,
        caller_count=caller_count,
        callee_count=callee_count,
        relations=groups,
        truncated=len(callers) < caller_count or len(callees) < callee_count,
    )


async def _resolve_symbol(session: AsyncSession, repo_id: str, symbol_id: str) -> GraphNode:
    """The symbol node with this id, else one sharing its bare name.

    Among same-named symbols, one in the id's own file wins; otherwise the
    lowest node id, so the pick is stable.
    """
    node = await crud.get_graph_node(session, repo_id, symbol_id)
    if node is not None and node.node_type == "symbol":
        return node

    parts = symbol_id.split("::")
    result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_type == "symbol",
            GraphNode.name == parts[-1],
        )
    )
    rows = list(result.scalars().all())
    if not rows:
        raise HTTPException(status_code=404, detail=f"Symbol not found: {symbol_id}")
    if len(parts) > 1:
        in_file = next((r for r in rows if r.file_path == parts[0]), None)
        if in_file is not None:
            return in_file
    return min(rows, key=lambda r: r.node_id)


def _wants_group(group: SymbolRelationGroup, edge_types: set[str], direction: str) -> bool:
    """Whether a relation group passes the edge-type filter and the direction."""
    if edge_types and group.edge_type not in edge_types:
        return False
    return direction == "both" or (direction == "callers") == (group.direction == "in")


def _symbol_summary(node: GraphNode) -> SymbolNodeSummary:
    """The response header for a symbol, degrading null columns to its id."""
    return SymbolNodeSummary(
        symbol_id=node.node_id,
        name=node.name or node.node_id,
        kind=node.kind or "unknown",
        file=node.file_path or node.node_id,
        start_line=node.start_line,
        signature=node.signature,
    )


@router.get("/{repo_id}/execution-flows", response_model=ExecutionFlowsResponse)
async def get_execution_flows(
    repo_id: str,
    top_n: int = Query(5, ge=1, le=20),
    max_depth: int = Query(5, ge=1, le=12),
    entry_point: str | None = Query(None, description="Specific symbol to trace from"),
    session: AsyncSession = Depends(get_db_session),
    _repo: object = Depends(with_repo),
) -> ExecutionFlowsResponse:
    """Return top entry points with BFS call-path traces."""
    if entry_point:
        node = await crud.get_graph_node(session, repo_id, entry_point)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Entry point not found: {entry_point}")
        entry_nodes = [node]
    else:
        entry_nodes = await crud.get_top_entry_points(session, repo_id, min_score=0.0, limit=top_n)

    if not entry_nodes:
        return ExecutionFlowsResponse(total_entry_points=0, flows=[])

    node_cache: dict[str, GraphNode] = {}
    flows = [
        await _trace_flow(session, repo_id, ep_node, max_depth, node_cache)
        for ep_node in entry_nodes
    ]
    flows.sort(key=lambda f: -f.entry_point_score)

    return ExecutionFlowsResponse(
        total_entry_points=len(flows),
        flows=flows,
    )


async def _trace_flow(
    session: AsyncSession,
    repo_id: str,
    ep_node: GraphNode,
    max_depth: int,
    node_cache: dict[str, GraphNode],
) -> ExecutionFlowEntry:
    """BFS the call path from one entry point, with per-hop origins."""
    hop_origins: dict[tuple[str, str], str] = {}
    termination: dict[str, Any] = {}
    trace = await bfs_trace(
        session,
        repo_id,
        ep_node.node_id,
        max_depth,
        node_cache,
        hop_origins,
        termination,
    )
    communities_visited, crosses = await resolve_trace_communities(
        session, repo_id, trace, node_cache
    )

    # Null rather than a list of nulls on an older index, so a consumer can
    # tell "no origins recorded" from "this hop has none".
    via = [hop_origins.get(pair) for pair in pairwise(trace)]

    return ExecutionFlowEntry(
        entry_point=ep_node.node_id,
        entry_point_name=ep_node.name or ep_node.node_id.split("::")[-1],
        entry_point_score=round(_ep_score(ep_node), 3),
        trace=trace,
        depth=len(trace) - 1,
        crosses_community=crosses,
        communities_visited=communities_visited,
        termination=termination.get("reason"),
        termination_detail=termination.get("detail") or None,
        trace_via=via if any(via) else None,
    )
