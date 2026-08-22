"""Full-graph export and node search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from repowise.core.analysis.execution_graph import is_walkable_execution_edge
from repowise.core.persistence import crud
from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.core.persistence.sql import LIKE_ESCAPE, escape_like
from repowise.server.deps import get_db_session
from repowise.server.mcp_server._graph_utils import _node_id_is_excluded
from repowise.server.routers.graph._common import with_repo
from repowise.server.schemas import GraphExportResponse, NodeSearchResult
from repowise.server.services.graph_views import edge_response
from repowise.server.services.node_signals import (
    EMPTY_SIGNALS,
    collect_node_signals,
    to_graph_node,
)

# Cap on full-graph export; above this we return a capped selection with
# truncated=True. Sized to keep the client-side force layout responsive;
# clients can step the limit up via the truncation banner.
_FULL_GRAPH_NODE_CAP = 1500

# When truncating, reserve up to this many slots per signal class (dead-code
# files, hotspots, execution-flow members) so flagged nodes survive selection
# even though their PageRank is low — dead code in particular is
# anti-correlated with PageRank, so a pure top-N cut would drop nearly all of
# it and overlays would silently render nothing. The remaining budget fills
# by PageRank as before.
_RESERVED_CLASS_CAP = 150

# Mirror the /execution-flows defaults the web client requests, so the
# reserved flow members match the traces the flows panel highlights.
_FLOW_ENTRY_POINTS = 10
_FLOW_MAX_DEPTH = 6

router = APIRouter()


def _flow_member_ids(
    edges: list[GraphEdge],
    entry_ids: list[str],
    max_depth: int = _FLOW_MAX_DEPTH,
) -> set[str]:
    """Files on the primary execution path from each entry point.

    In-memory mirror of ``mcp_server._graph_utils.bfs_trace`` (same rules:
    ``calls`` edges only, highest-confidence unvisited successor, confidence
    >= 0.5, test/demo paths excluded) over the already-fetched edge list, so
    the export doesn't re-query edges per hop.

    ``calls`` edges only ever join symbol nodes, so the trace itself is a list
    of ``path/to/file.py::symbol`` ids. The export carries files, so each step
    is reduced to its containing file — the reservation is for "the files this
    flow runs through", which is what the canvas can actually highlight.
    """
    adjacency: dict[str, list[GraphEdge]] = {}
    for e in edges:
        if is_walkable_execution_edge(e.edge_type, e.resolution_origin, e.confidence):
            adjacency.setdefault(e.source_node_id, []).append(e)

    members: set[str] = set()
    for entry_id in entry_ids:
        visited: set[str] = {entry_id}
        current = entry_id
        for _ in range(max_depth):
            best_id: str | None = None
            best_conf = -1.0
            for e in adjacency.get(current, ()):
                tid = e.target_node_id
                conf = e.confidence if e.confidence is not None else 0.0
                if tid in visited or _node_id_is_excluded(tid):
                    continue
                if conf > best_conf:
                    best_conf = conf
                    best_id = tid
            if best_id is None:
                break
            visited.add(best_id)
            current = best_id
        members |= visited
    return {node_id.split("::")[0] for node_id in members}


@router.get("/{repo_id}/nodes/search", response_model=list[NodeSearchResult])
async def search_nodes(
    repo_id: str,
    q: str = Query(..., description="Search query"),
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_db_session),
    _repo: object = Depends(with_repo),
) -> list[NodeSearchResult]:
    """Full-text search over node_id values."""
    result = await session.execute(
        select(GraphNode)
        .where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_id.ilike(f"%{escape_like(q)}%", escape=LIKE_ESCAPE),
        )
        .order_by(GraphNode.symbol_count.desc(), GraphNode.pagerank.desc())
        .limit(limit)
    )
    nodes = result.scalars().all()
    return [
        NodeSearchResult(node_id=n.node_id, language=n.language, symbol_count=n.symbol_count)
        for n in nodes
    ]


@router.get("/{repo_id}", response_model=GraphExportResponse)
async def export_graph(
    repo_id: str,
    limit: int = Query(
        _FULL_GRAPH_NODE_CAP,
        ge=1,
        le=6000,
        description="Maximum nodes to return. Stepped up by the client.",
    ),
    session: AsyncSession = Depends(get_db_session),
    _repo: object = Depends(with_repo),
) -> GraphExportResponse:
    """Export the full dependency graph in D3 force-directed format.

    File nodes only. ``graph_nodes`` also holds a symbol node per extracted
    symbol — on this repo 28,205 of 31,397 rows — and PageRank ranks the two
    kinds together, so 450 of the top 1,500 used to be symbols drawn as file
    circles, labelled ``path/file.py::Symbol`` and carrying a ``fullPath`` that
    is not a path. Nothing downstream reads ``node_type`` (checked: packages/ui
    and the VS Code webview), so the client cannot tell them apart. Excluding
    them buys back those 1,500 slots for real files and makes
    ``total_node_count`` a count of files, which is what every caller labels it.

    Large repos are capped with ``truncated=True``. Selection reserves slots
    for dead-code files, hotspots, and execution-flow members (up to
    ``_RESERVED_CLASS_CAP`` each, highest PageRank first within a class) and
    fills the rest by PageRank, so signal overlays always have their nodes in
    view. ``dead_total``/``hot_total`` vs ``*_in_view`` let clients say
    "showing 12 of 37" instead of rendering silently-empty overlays.
    """
    # Load only the columns this endpoint actually serializes. The row carries
    # several large text columns (signature, qualified_name, file_path,
    # community_meta_json) that nothing here reads, and this query hydrates
    # EVERY node in the repo before capping — on a 31k-node repo that is several
    # MB of text fetched and discarded per request. The list below must stay a
    # superset of what `node_to_response` reads plus `node_id`: a deferred
    # column accessed under asyncio raises MissingGreenlet rather than lazily
    # loading, so adding a field there means adding it here.
    #
    # Caveat worth knowing before trusting the saving: on the truncated path
    # `crud.get_top_entry_points` runs its own unnarrowed query over every
    # symbol node to read `community_meta_json`. Those rows are already in the
    # identity map, so SQLAlchemy back-fills their deferred columns and the
    # saving applies only to file nodes there. Correctness is unaffected.
    node_result = await session.execute(
        select(GraphNode)
        .options(
            load_only(
                GraphNode.node_id,
                GraphNode.node_type,
                GraphNode.language,
                GraphNode.symbol_count,
                GraphNode.is_test,
                GraphNode.is_entry_point,
                GraphNode.pagerank,
                GraphNode.betweenness,
                GraphNode.community_id,
            )
        )
        .where(GraphNode.repository_id == repo_id, GraphNode.node_type == "file")
        .order_by(GraphNode.pagerank.desc())
    )
    all_nodes = node_result.scalars().all()
    total_node_count = len(all_nodes)
    truncated = total_node_count > limit

    edge_result = await session.execute(select(GraphEdge).where(GraphEdge.repository_id == repo_id))
    edges = list(edge_result.scalars().all())

    # Repo-wide signals: needed for the *_total counts and, when truncating,
    # for reserved-slot selection (flagged nodes are known before the cut).
    signals = await collect_node_signals(session, repo_id, None)

    def _sig(n: GraphNode):
        return signals.get(n.node_id, EMPTY_SIGNALS)

    dead_total = sum(1 for n in all_nodes if _sig(n).is_dead)
    hot_total = sum(1 for n in all_nodes if _sig(n).is_hotspot)

    if truncated:
        entry_nodes = await crud.get_top_entry_points(
            session, repo_id, min_score=0.0, limit=_FLOW_ENTRY_POINTS
        )
        flow_ids = _flow_member_ids(edges, [n.node_id for n in entry_nodes])

        kept_ids: set[str] = set()
        flagged_classes = (
            lambda n: _sig(n).is_dead,
            lambda n: _sig(n).is_hotspot,
            lambda n: n.node_id in flow_ids,
        )
        for flagged in flagged_classes:
            picked = 0
            for n in all_nodes:  # PageRank order → best flagged nodes first
                if picked >= _RESERVED_CLASS_CAP or len(kept_ids) >= limit:
                    break
                if n.node_id not in kept_ids and flagged(n):
                    kept_ids.add(n.node_id)
                    picked += 1
        for n in all_nodes:
            if len(kept_ids) >= limit:
                break
            kept_ids.add(n.node_id)
        nodes = [n for n in all_nodes if n.node_id in kept_ids]
    else:
        nodes = list(all_nodes)
        kept_ids = {n.node_id for n in nodes}

    node_responses = [to_graph_node(n, _sig(n)) for n in nodes]

    link_responses = [
        edge_response(e)
        for e in edges
        if e.source_node_id in kept_ids and e.target_node_id in kept_ids
    ]

    return GraphExportResponse(
        nodes=node_responses,
        links=link_responses,
        truncated=truncated,
        total_node_count=total_node_count,
        dead_total=dead_total,
        dead_in_view=sum(1 for n in nodes if _sig(n).is_dead),
        hot_total=hot_total,
        hot_in_view=sum(1 for n in nodes if _sig(n).is_hotspot),
    )
