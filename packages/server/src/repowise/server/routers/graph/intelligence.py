"""Symbol-level graph intelligence: metrics, callers/callees, execution flows."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    CallerCalleeEntry,
    CallersCalleesResponse,
    ExecutionFlowEntry,
    ExecutionFlowsResponse,
    GraphMetricsResponse,
    SymbolNodeSummary,
)

router = APIRouter()


@router.get("/{repo_id}/metrics", response_model=GraphMetricsResponse)
async def get_graph_metrics(
    repo_id: str,
    node_id: str = Query(..., description="File path or symbol_id"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
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

    degrees = await crud.get_node_degree_counts(session, repo_id, node_id)
    meta = parse_community_meta(node)

    return GraphMetricsResponse(
        target=node_id,
        node_type=node.node_type or "file",
        pagerank=round(node.pagerank or 0.0, 6),
        pagerank_percentile=percentile_rank(node.pagerank or 0.0, all_pr),
        betweenness=round(node.betweenness or 0.0, 6),
        betweenness_percentile=percentile_rank(node.betweenness or 0.0, all_bw),
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
    edge_types: str = Query("calls", description="Comma-separated edge types"),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    _repo: object = Depends(with_repo),
) -> CallersCalleesResponse:
    """Find who calls a symbol and what it calls. Also works for class hierarchy."""
    if direction not in ("callers", "callees", "both"):
        direction = "both"

    et_list = [t.strip() for t in edge_types.split(",") if t.strip()]
    if not et_list:
        et_list = ["calls"]

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

    edges = await crud.get_graph_edges_for_node(
        session,
        repo_id,
        node.node_id,
        direction=direction,
        edge_types=et_list,
        limit=limit,
    )

    # Hydrate connected nodes
    other_ids: set[str] = set()
    for e in edges:
        if e.source_node_id != node.node_id:
            other_ids.add(e.source_node_id)
        if e.target_node_id != node.node_id:
            other_ids.add(e.target_node_id)

    node_map = await crud.get_graph_nodes_by_ids(session, repo_id, list(other_ids))

    callers: list[CallerCalleeEntry] = []
    callees: list[CallerCalleeEntry] = []

    for e in edges:
        is_caller = e.target_node_id == node.node_id
        other_id = e.source_node_id if is_caller else e.target_node_id
        other = node_map.get(other_id)

        entry = CallerCalleeEntry(
            symbol_id=other_id,
            name=other.name
            if other
            else (other_id.split("::")[-1] if "::" in other_id else other_id),
            kind=other.kind if other else "unknown",
            file=other.file_path
            if other
            else (other_id.split("::")[0] if "::" in other_id else other_id),
            start_line=other.start_line if other else None,
            edge_type=e.edge_type or "calls",
            confidence=round(e.confidence or 0.0, 3),
        )

        if is_caller:
            callers.append(entry)
        else:
            callees.append(entry)

    callers.sort(key=lambda x: (-x.confidence, x.name))
    callees.sort(key=lambda x: (-x.confidence, x.name))

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
        callers=callers if direction in ("callers", "both") else [],
        callees=callees if direction in ("callees", "both") else [],
        caller_count=len(callers),
        callee_count=len(callees),
        truncated=(len(callers) >= limit or len(callees) >= limit),
    )


@router.get("/{repo_id}/execution-flows", response_model=ExecutionFlowsResponse)
async def get_execution_flows(
    repo_id: str,
    top_n: int = Query(5, ge=1, le=20),
    max_depth: int = Query(5, ge=1, le=12),
    entry_point: str | None = Query(None, description="Specific symbol to trace from"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
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
        trace = await bfs_trace(session, repo_id, ep_node.node_id, max_depth, node_cache)
        communities_visited, crosses = await resolve_trace_communities(
            session, repo_id, trace, node_cache
        )

        flows.append(
            ExecutionFlowEntry(
                entry_point=ep_node.node_id,
                entry_point_name=ep_node.name or ep_node.node_id.split("::")[-1],
                entry_point_score=round(ep_score, 3),
                trace=trace,
                depth=len(trace) - 1,
                crosses_community=crosses,
                communities_visited=communities_visited,
            )
        )

    flows.sort(key=lambda f: -f.entry_point_score)

    return ExecutionFlowsResponse(
        total_entry_points=len(flows),
        flows=flows,
    )
