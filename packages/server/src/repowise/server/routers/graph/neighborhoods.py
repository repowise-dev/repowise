"""Neighborhood-style graph views: ego, entry-points, dead-code, hot-files."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import (
    DeadCodeFinding,
    GitMetadata,
    GraphEdge,
    GraphNode,
)
from repowise.server.deps import get_db_session
from repowise.server.routers.graph._common import _edge_response, with_repo
from repowise.server.routers.graph.signals import (
    _EMPTY_SIGNALS,
    _collect_node_signals,
    _node_to_response,
    _to_graph_node,
)
from repowise.server.schemas import (
    DeadCodeGraphNodeResponse,
    DeadCodeGraphResponse,
    EgoGraphResponse,
    GitMetadataResponse,
    GraphEdgeResponse,
    GraphExportResponse,
    HotFilesGraphResponse,
    HotFilesNodeResponse,
)

router = APIRouter()


@router.get("/{repo_id}/ego", response_model=EgoGraphResponse)
async def ego_graph(
    repo_id: str,
    node_id: str = Query(..., description="Center node ID"),
    hops: int = Query(2, ge=1, le=3),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    _repo: object = Depends(with_repo),
) -> EgoGraphResponse:
    """Return the N-hop neighborhood of a given node."""
    try:
        import networkx as nx
    except ImportError:
        raise HTTPException(status_code=501, detail="networkx not available") from None

    edge_result = await session.execute(select(GraphEdge).where(GraphEdge.repository_id == repo_id))
    edges = edge_result.scalars().all()

    graph: nx.DiGraph = nx.DiGraph()
    for e in edges:
        graph.add_edge(e.source_node_id, e.target_node_id)

    if node_id not in graph:
        node_check = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repo_id,
                GraphNode.node_id == node_id,
            )
        )
        if node_check.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")
        graph.add_node(node_id)

    inbound_count = graph.in_degree(node_id)
    outbound_count = graph.out_degree(node_id)

    ego: nx.DiGraph = nx.ego_graph(graph, node_id, radius=hops, undirected=True)
    ego_node_ids = set(ego.nodes())

    node_result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_id.in_(list(ego_node_ids)),
        )
    )
    node_rows = node_result.scalars().all()

    ego_edge_responses = [
        _edge_response(e)
        for e in edges
        if e.source_node_id in ego_node_ids and e.target_node_id in ego_node_ids
    ]

    git_result = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repo_id,
            GitMetadata.file_path == node_id,
        )
    )
    git_row = git_result.scalar_one_or_none()
    git_meta = GitMetadataResponse.from_orm(git_row) if git_row else None

    signals = await _collect_node_signals(session, repo_id, list(ego_node_ids))

    node_responses = [_to_graph_node(n, signals.get(n.node_id, _EMPTY_SIGNALS)) for n in node_rows]

    return EgoGraphResponse(
        nodes=node_responses,
        links=ego_edge_responses,
        center_node_id=node_id,
        center_git_meta=git_meta,
        inbound_count=inbound_count,
        outbound_count=outbound_count,
    )


@router.get("/{repo_id}/entry-points", response_model=GraphExportResponse)
async def entry_points_graph(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    _repo: object = Depends(with_repo),
) -> GraphExportResponse:
    """Return the subgraph reachable within 3 hops from entry-point nodes."""
    try:
        import networkx as nx
    except ImportError:
        raise HTTPException(status_code=501, detail="networkx not available") from None

    edge_result = await session.execute(select(GraphEdge).where(GraphEdge.repository_id == repo_id))
    edges = edge_result.scalars().all()

    graph: nx.DiGraph = nx.DiGraph()
    for e in edges:
        graph.add_edge(e.source_node_id, e.target_node_id)

    ep_result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repo_id,
            GraphNode.is_entry_point == True,  # noqa: E712
        )
    )
    entry_nodes = ep_result.scalars().all()

    reachable: set[str] = set()
    for ep in entry_nodes:
        reachable.add(ep.node_id)
        if ep.node_id in graph:
            paths = nx.single_source_shortest_path_length(graph, ep.node_id, cutoff=3)
            reachable.update(paths.keys())

    if not reachable:
        return GraphExportResponse(nodes=[], links=[])

    node_result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_id.in_(list(reachable)),
        )
    )
    nodes = node_result.scalars().all()

    signals = await _collect_node_signals(session, repo_id, list(reachable))

    node_responses = [_to_graph_node(n, signals.get(n.node_id, _EMPTY_SIGNALS)) for n in nodes]

    link_responses = [
        _edge_response(e)
        for e in edges
        if e.source_node_id in reachable and e.target_node_id in reachable
    ]

    return GraphExportResponse(
        nodes=node_responses,
        links=link_responses,
        total_node_count=len(node_responses),
    )


@router.get("/{repo_id}/dead-nodes", response_model=DeadCodeGraphResponse)
async def dead_code_graph(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    _repo: object = Depends(with_repo),
) -> DeadCodeGraphResponse:
    """Return dead-code nodes plus their 1-hop neighbors."""
    finding_result = await session.execute(
        select(DeadCodeFinding).where(
            DeadCodeFinding.repository_id == repo_id,
            DeadCodeFinding.status == "open",
            DeadCodeFinding.kind == "unreachable_file",
        )
    )
    findings = finding_result.scalars().all()

    if not findings:
        return DeadCodeGraphResponse(nodes=[], links=[])

    # Only consider high-confidence findings
    findings = [f for f in findings if f.confidence >= 0.85]
    if not findings:
        return DeadCodeGraphResponse(nodes=[], links=[])

    dead_paths = {f.file_path for f in findings}
    finding_map: dict[str, DeadCodeFinding] = {f.file_path: f for f in findings}

    node_result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_id.in_(list(dead_paths)),
        )
    )
    all_candidates = node_result.scalars().all()

    # Filter out false positives:
    # - Entry points and test files are never truly dead
    # - Files with incoming edges (in_degree > 0) are used
    # - Framework files (Next.js pages/layouts, alembic, routers, etc.)
    _framework_patterns = (
        "alembic/versions/",
        "__init__.py",
        "conftest.py",
        "fixtures/",
        "/app/",  # Next.js app router pages
        "/pages/",  # Next.js pages router
        "/routers/",  # FastAPI routers
        "/commands/",  # CLI commands
        "/components/ui/",  # UI component library
    )
    dead_nodes = [
        n
        for n in all_candidates
        if not n.is_entry_point
        and not n.is_test
        and not any(pat in n.node_id for pat in _framework_patterns)
    ]
    dead_node_ids = {n.node_id for n in dead_nodes}

    if not dead_node_ids:
        return DeadCodeGraphResponse(nodes=[], links=[])

    out_edge_result = await session.execute(
        select(GraphEdge).where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.source_node_id.in_(list(dead_node_ids)),
        )
    )
    in_edge_result = await session.execute(
        select(GraphEdge).where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.target_node_id.in_(list(dead_node_ids)),
        )
    )
    all_edges = list(out_edge_result.scalars().all()) + list(in_edge_result.scalars().all())

    neighbor_ids: set[str] = set()
    for e in all_edges:
        neighbor_ids.add(e.source_node_id)
        neighbor_ids.add(e.target_node_id)
    neighbor_ids -= dead_node_ids

    if neighbor_ids:
        nbr_result = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repo_id,
                GraphNode.node_id.in_(list(neighbor_ids)),
            )
        )
        neighbor_nodes = nbr_result.scalars().all()
    else:
        neighbor_nodes = []

    all_node_ids = dead_node_ids | neighbor_ids
    signals = await _collect_node_signals(session, repo_id, list(all_node_ids))

    def _to_dead_node(n: GraphNode, confidence_group: str) -> DeadCodeGraphNodeResponse:
        return _node_to_response(
            n,
            signals.get(n.node_id, _EMPTY_SIGNALS),
            DeadCodeGraphNodeResponse,
            confidence_group=confidence_group,
        )

    node_responses: list[DeadCodeGraphNodeResponse] = []
    for n in dead_nodes:
        finding = finding_map.get(n.node_id)
        confidence = finding.confidence if finding else 0.0
        node_responses.append(_to_dead_node(n, "certain" if confidence >= 0.85 else "likely"))
    for n in neighbor_nodes:
        node_responses.append(_to_dead_node(n, "neighbor"))

    seen: set[tuple[str, str]] = set()
    link_responses: list[GraphEdgeResponse] = []
    for e in all_edges:
        key = (e.source_node_id, e.target_node_id)
        if (
            key not in seen
            and e.source_node_id in all_node_ids
            and e.target_node_id in all_node_ids
        ):
            seen.add(key)
            link_responses.append(_edge_response(e))

    return DeadCodeGraphResponse(nodes=node_responses, links=link_responses)


@router.get("/{repo_id}/hot-files", response_model=HotFilesGraphResponse)
async def hot_files_graph(
    repo_id: str,
    days: int = Query(30, description="Time window in days: 7, 30, or 90"),
    limit: int = Query(25, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    _repo: object = Depends(with_repo),
) -> HotFilesGraphResponse:
    """Return the most-committed files plus their 1-hop outgoing neighbors."""
    commit_col = GitMetadata.commit_count_90d if days > 30 else GitMetadata.commit_count_30d

    git_result = await session.execute(
        select(GitMetadata)
        .where(GitMetadata.repository_id == repo_id)
        .order_by(commit_col.desc())
        .limit(limit)
    )
    hot_files = git_result.scalars().all()

    if not hot_files:
        return HotFilesGraphResponse(nodes=[], links=[])

    hot_paths = {gm.file_path for gm in hot_files}
    commit_map: dict[str, int] = {
        gm.file_path: (gm.commit_count_90d if days > 30 else gm.commit_count_30d)
        for gm in hot_files
    }

    node_result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_id.in_(list(hot_paths)),
        )
    )
    hot_nodes = node_result.scalars().all()
    hot_node_ids = {n.node_id for n in hot_nodes}

    out_edge_result = await session.execute(
        select(GraphEdge).where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.source_node_id.in_(list(hot_node_ids)),
        )
    )
    out_edges = out_edge_result.scalars().all()

    neighbor_ids = {e.target_node_id for e in out_edges} - hot_node_ids
    if neighbor_ids:
        nbr_result = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repo_id,
                GraphNode.node_id.in_(list(neighbor_ids)),
            )
        )
        neighbor_nodes = nbr_result.scalars().all()
    else:
        neighbor_nodes = []

    all_node_ids = hot_node_ids | neighbor_ids
    signals = await _collect_node_signals(session, repo_id, list(all_node_ids))

    def _to_hot_node(n: GraphNode, commit_count: int) -> HotFilesNodeResponse:
        return _node_to_response(
            n,
            signals.get(n.node_id, _EMPTY_SIGNALS),
            HotFilesNodeResponse,
            commit_count=commit_count,
        )

    node_responses = [_to_hot_node(n, commit_map.get(n.node_id, 0)) for n in hot_nodes] + [
        _to_hot_node(n, 0) for n in neighbor_nodes
    ]

    link_responses = [_edge_response(e) for e in out_edges if e.target_node_id in all_node_ids]

    return HotFilesGraphResponse(nodes=node_responses, links=link_responses)
