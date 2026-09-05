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

    # Percentiles computed against all file-type nodes
    all_files = await crud.get_all_file_metrics(session, repo_id)
    all_pr = [n.pagerank or 0.0 for n in all_files]
    all_bw = [n.betweenness or 0.0 for n in all_files]

    # Scoped by layer. This endpoint feeds the same symbol component as
    # /api/symbols/detail (the drawer, where that page is the drill-down), so
    # an unscoped count here made one symbol report two different degrees
    # depending on which of the two the user opened.
    degrees = await crud.get_node_degree_counts(
        session,
        repo_id,
        node_id,
        edge_types=(
            sorted(SYMBOL_USE_EDGE_TYPES)
            if node.node_type == "symbol"
            else sorted(FILE_DEPENDENCY_EDGE_TYPES)
        ),
    )
    meta = parse_community_meta(node)

    return GraphMetricsResponse(
        target=node_id,
        node_type=node.node_type or "file",
        pagerank=round(node.pagerank or 0.0, 6),
        pagerank_percentile=percentile_rank(node.pagerank or 0.0, all_pr),
        betweenness=round(node.betweenness or 0.0, 6),
        betweenness_percentile=percentile_rank(node.betweenness or 0.0, all_bw),
        betweenness_scored=node.betweenness_commit is not None,
        community_id=node.community_id or 0,
        community_label=meta.get("label") or None,
        is_entry_point=node.is_entry_point,
        in_degree=degrees["in_degree"],
        out_degree=degrees["out_degree"],
        entry_point_score=meta.get("entry_point_score"),
        kind=node.kind if node.node_type == "symbol" else None,
        file=node.file_path if node.node_type == "symbol" else None,
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

    # Resolve symbol: exact then fuzzy
    node = await crud.get_graph_node(session, repo_id, symbol_id)
    if node is None or node.node_type != "symbol":
        # Fuzzy: try bare name
        bare = symbol_id.split("::")[-1] if "::" in symbol_id else symbol_id
        result = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repo_id,
                GraphNode.node_type == "symbol",
                GraphNode.name == bare,
            )
        )
        rows = list(result.scalars().all())
        if not rows:
            raise HTTPException(status_code=404, detail=f"Symbol not found: {symbol_id}")
        if "::" in symbol_id:
            file_hint = symbol_id.split("::")[0]
            for r in rows:
                if r.file_path == file_hint:
                    node = r
                    break
        if node is None or node.node_type != "symbol":
            rows.sort(key=lambda r: r.node_id)
            node = rows[0]

    relations = await load_symbol_relations(
        session, repo_id, node.node_id, present=True, call_row_cap=limit
    )
    groups = [
        g
        for g in relations.groups
        if (not et_filter or g.edge_type in et_filter)
        and (direction == "both" or (direction == "callers") == (g.direction == "in"))
    ]

    wants_in = direction in ("callers", "both")
    wants_out = direction in ("callees", "both")
    return CallersCalleesResponse(
        symbol_id=node.node_id,
        symbol=SymbolNodeSummary(
            symbol_id=node.node_id,
            name=node.name or node.node_id,
            kind=node.kind or "unknown",
            file=node.file_path or node.node_id,
            start_line=node.start_line,
            signature=node.signature,
        ),
        callers=relations.callers if wants_in else [],
        callees=relations.callees if wants_out else [],
        caller_count=relations.caller_total if wants_in else 0,
        callee_count=relations.callee_total if wants_out else 0,
        relations=groups,
        truncated=(
            (wants_in and len(relations.callers) < relations.caller_total)
            or (wants_out and len(relations.callees) < relations.callee_total)
        ),
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
    entry_nodes: list[tuple[GraphNode, float]] = []

    if entry_point:
        node = await crud.get_graph_node(session, repo_id, entry_point)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Entry point not found: {entry_point}")
        entry_nodes = [(node, _ep_score(node))]
    else:
        top_nodes = await crud.get_top_entry_points(session, repo_id, min_score=0.0, limit=top_n)
        for n in top_nodes:
            entry_nodes.append((n, _ep_score(n)))

    if not entry_nodes:
        return ExecutionFlowsResponse(total_entry_points=0, flows=[])

    node_cache: dict[str, GraphNode] = {}
    flows: list[ExecutionFlowEntry] = []

    for ep_node, ep_score in entry_nodes:
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

        flows.append(
            ExecutionFlowEntry(
                entry_point=ep_node.node_id,
                entry_point_name=ep_node.name or ep_node.node_id.split("::")[-1],
                entry_point_score=round(ep_score, 3),
                trace=trace,
                depth=len(trace) - 1,
                crosses_community=crosses,
                communities_visited=communities_visited,
                termination=termination.get("reason"),
                termination_detail=termination.get("detail") or None,
                trace_via=via if any(via) else None,
            )
        )

    flows.sort(key=lambda f: -f.entry_point_score)

    return ExecutionFlowsResponse(
        total_entry_points=len(flows),
        flows=flows,
    )
