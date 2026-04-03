"""/api/graph — Dependency graph export in D3-compatible format."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.core.persistence.models import (
    DeadCodeFinding,
    GitMetadata,
    GraphEdge,
    GraphNode,
    Page,
)
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import (
    DeadCodeGraphNodeResponse,
    DeadCodeGraphResponse,
    EgoGraphResponse,
    GitMetadataResponse,
    GraphEdgeResponse,
    GraphExportResponse,
    GraphNodeResponse,
    HotFilesGraphResponse,
    HotFilesNodeResponse,
    ModuleEdgeResponse,
    ModuleGraphResponse,
    ModuleNodeResponse,
    NodeSearchResult,
)

router = APIRouter(
    prefix="/api/graph",
    tags=["graph"],
    dependencies=[Depends(verify_api_key)],
)


def _parse_imported_names(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


async def _get_documented_paths(session: AsyncSession, repo_id: str) -> set[str]:
    """Return the set of node_ids (file paths) that have a wiki page."""
    result = await session.execute(select(Page.target_path).where(Page.repository_id == repo_id))
    return {row.target_path for row in result.all() if row.target_path}


# ---------------------------------------------------------------------------
# Module graph
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/modules", response_model=ModuleGraphResponse)
async def module_graph(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ModuleGraphResponse:
    """Collapsed directory-level graph: one node per top-level path segment."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    node_result = await session.execute(select(GraphNode).where(GraphNode.repository_id == repo_id))
    nodes = node_result.scalars().all()

    # Group nodes by first path segment
    modules: dict[str, list[GraphNode]] = {}
    for n in nodes:
        parts = n.node_id.split("/")
        module = parts[0] if len(parts) > 1 else n.node_id
        modules.setdefault(module, []).append(n)

    # Fetch page confidence for doc coverage
    page_result = await session.execute(
        select(Page.target_path, Page.confidence).where(Page.repository_id == repo_id)
    )
    page_coverage: dict[str, float] = {row.target_path: row.confidence for row in page_result.all()}

    node_to_module: dict[str, str] = {}
    module_nodes: list[ModuleNodeResponse] = []
    for module_id, module_file_nodes in modules.items():
        file_count = len(module_file_nodes)
        symbol_count = sum(n.symbol_count for n in module_file_nodes)
        avg_pagerank = sum(n.pagerank for n in module_file_nodes) / max(file_count, 1)
        covered = sum(1 for n in module_file_nodes if page_coverage.get(n.node_id, 0.0) >= 0.7)
        doc_coverage_pct = covered / max(file_count, 1)

        for n in module_file_nodes:
            node_to_module[n.node_id] = module_id

        module_nodes.append(
            ModuleNodeResponse(
                module_id=module_id,
                file_count=file_count,
                symbol_count=symbol_count,
                avg_pagerank=avg_pagerank,
                doc_coverage_pct=doc_coverage_pct,
            )
        )

    # Build inter-module edges from file-level edges
    edge_result = await session.execute(select(GraphEdge).where(GraphEdge.repository_id == repo_id))
    edges = edge_result.scalars().all()

    edge_counts: dict[tuple[str, str], int] = {}
    for e in edges:
        src_module = node_to_module.get(e.source_node_id)
        tgt_module = node_to_module.get(e.target_node_id)
        if src_module and tgt_module and src_module != tgt_module:
            key = (src_module, tgt_module)
            edge_counts[key] = edge_counts.get(key, 0) + 1

    module_edges = [
        ModuleEdgeResponse(source=src, target=tgt, edge_count=count)
        for (src, tgt), count in edge_counts.items()
    ]

    return ModuleGraphResponse(nodes=module_nodes, edges=module_edges)


# ---------------------------------------------------------------------------
# Ego / neighborhood graph
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/ego", response_model=EgoGraphResponse)
async def ego_graph(
    repo_id: str,
    node_id: str = Query(..., description="Center node ID"),
    hops: int = Query(2, ge=1, le=3),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> EgoGraphResponse:
    """Return the N-hop neighborhood of a given node."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

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
        GraphEdgeResponse(
            source=e.source_node_id,
            target=e.target_node_id,
            imported_names=_parse_imported_names(e.imported_names_json),
        )
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

    documented = await _get_documented_paths(session, repo_id)

    node_responses = [
        GraphNodeResponse(
            node_id=n.node_id,
            node_type=n.node_type,
            language=n.language,
            symbol_count=n.symbol_count,
            pagerank=n.pagerank,
            betweenness=n.betweenness,
            community_id=n.community_id,
            is_test=n.is_test,
            is_entry_point=n.is_entry_point,
            has_doc=n.node_id in documented,
        )
        for n in node_rows
    ]

    return EgoGraphResponse(
        nodes=node_responses,
        links=ego_edge_responses,
        center_node_id=node_id,
        center_git_meta=git_meta,
        inbound_count=inbound_count,
        outbound_count=outbound_count,
    )


# ---------------------------------------------------------------------------
# Architecture / entry-point view
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/entry-points", response_model=GraphExportResponse)
async def entry_points_graph(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> GraphExportResponse:
    """Return the subgraph reachable within 3 hops from entry-point nodes."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

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

    documented = await _get_documented_paths(session, repo_id)

    node_responses = [
        GraphNodeResponse(
            node_id=n.node_id,
            node_type=n.node_type,
            language=n.language,
            symbol_count=n.symbol_count,
            pagerank=n.pagerank,
            betweenness=n.betweenness,
            community_id=n.community_id,
            is_test=n.is_test,
            is_entry_point=n.is_entry_point,
            has_doc=n.node_id in documented,
        )
        for n in nodes
    ]

    link_responses = [
        GraphEdgeResponse(
            source=e.source_node_id,
            target=e.target_node_id,
            imported_names=_parse_imported_names(e.imported_names_json),
        )
        for e in edges
        if e.source_node_id in reachable and e.target_node_id in reachable
    ]

    return GraphExportResponse(nodes=node_responses, links=link_responses)


# ---------------------------------------------------------------------------
# Dead code graph
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/dead-nodes", response_model=DeadCodeGraphResponse)
async def dead_code_graph(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> DeadCodeGraphResponse:
    """Return dead-code nodes plus their 1-hop neighbors."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

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
        "/app/",        # Next.js app router pages
        "/pages/",      # Next.js pages router
        "/routers/",    # FastAPI routers
        "/commands/",   # CLI commands
        "/components/ui/",  # UI component library
    )
    dead_nodes = [
        n for n in all_candidates
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
    documented = await _get_documented_paths(session, repo_id)

    def _to_dead_node(n: GraphNode, confidence_group: str) -> DeadCodeGraphNodeResponse:
        return DeadCodeGraphNodeResponse(
            node_id=n.node_id,
            node_type=n.node_type,
            language=n.language,
            symbol_count=n.symbol_count,
            pagerank=n.pagerank,
            betweenness=n.betweenness,
            community_id=n.community_id,
            is_test=n.is_test,
            is_entry_point=n.is_entry_point,
            has_doc=n.node_id in documented,
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
            link_responses.append(
                GraphEdgeResponse(
                    source=e.source_node_id,
                    target=e.target_node_id,
                    imported_names=_parse_imported_names(e.imported_names_json),
                )
            )

    return DeadCodeGraphResponse(nodes=node_responses, links=link_responses)


# ---------------------------------------------------------------------------
# Hot files graph
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/hot-files", response_model=HotFilesGraphResponse)
async def hot_files_graph(
    repo_id: str,
    days: int = Query(30, description="Time window in days: 7, 30, or 90"),
    limit: int = Query(25, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HotFilesGraphResponse:
    """Return the most-committed files plus their 1-hop outgoing neighbors."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

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
    documented = await _get_documented_paths(session, repo_id)

    def _to_hot_node(n: GraphNode, commit_count: int) -> HotFilesNodeResponse:
        return HotFilesNodeResponse(
            node_id=n.node_id,
            node_type=n.node_type,
            language=n.language,
            symbol_count=n.symbol_count,
            pagerank=n.pagerank,
            betweenness=n.betweenness,
            community_id=n.community_id,
            is_test=n.is_test,
            is_entry_point=n.is_entry_point,
            has_doc=n.node_id in documented,
            commit_count=commit_count,
        )

    node_responses = [_to_hot_node(n, commit_map.get(n.node_id, 0)) for n in hot_nodes] + [
        _to_hot_node(n, 0) for n in neighbor_nodes
    ]

    link_responses = [
        GraphEdgeResponse(
            source=e.source_node_id,
            target=e.target_node_id,
            imported_names=_parse_imported_names(e.imported_names_json),
        )
        for e in out_edges
        if e.target_node_id in all_node_ids
    ]

    return HotFilesGraphResponse(nodes=node_responses, links=link_responses)


# ---------------------------------------------------------------------------
# Node search
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/nodes/search", response_model=list[NodeSearchResult])
async def search_nodes(
    repo_id: str,
    q: str = Query(..., description="Search query"),
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[NodeSearchResult]:
    """Full-text search over node_id values."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    result = await session.execute(
        select(GraphNode)
        .where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_id.ilike(f"%{q}%"),
        )
        .order_by(GraphNode.symbol_count.desc(), GraphNode.pagerank.desc())
        .limit(limit)
    )
    nodes = result.scalars().all()
    return [
        NodeSearchResult(node_id=n.node_id, language=n.language, symbol_count=n.symbol_count)
        for n in nodes
    ]


# ---------------------------------------------------------------------------
# Full graph export
# ---------------------------------------------------------------------------


@router.get("/{repo_id}", response_model=GraphExportResponse)
async def export_graph(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> GraphExportResponse:
    """Export the full dependency graph in D3 force-directed format."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    node_result = await session.execute(select(GraphNode).where(GraphNode.repository_id == repo_id))
    nodes = node_result.scalars().all()

    edge_result = await session.execute(select(GraphEdge).where(GraphEdge.repository_id == repo_id))
    edges = edge_result.scalars().all()

    documented = await _get_documented_paths(session, repo_id)

    node_responses = [
        GraphNodeResponse(
            node_id=n.node_id,
            node_type=n.node_type,
            language=n.language,
            symbol_count=n.symbol_count,
            pagerank=n.pagerank,
            betweenness=n.betweenness,
            community_id=n.community_id,
            is_test=n.is_test,
            is_entry_point=n.is_entry_point,
            has_doc=n.node_id in documented,
        )
        for n in nodes
    ]

    link_responses = [
        GraphEdgeResponse(
            source=e.source_node_id,
            target=e.target_node_id,
            imported_names=_parse_imported_names(e.imported_names_json),
        )
        for e in edges
    ]

    return GraphExportResponse(nodes=node_responses, links=link_responses)


# ---------------------------------------------------------------------------
# Shortest path
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/path")
async def dependency_path(
    repo_id: str,
    source: str = Query(..., alias="from", description="Source node ID"),
    target: str = Query(..., alias="to", description="Target node ID"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Find the shortest dependency path between two nodes.

    When no direct path exists, returns visual context with nearest common
    ancestors, shared neighbors, and bridge suggestions.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    edge_result = await session.execute(select(GraphEdge).where(GraphEdge.repository_id == repo_id))
    edges = edge_result.scalars().all()

    node_result = await session.execute(select(GraphNode).where(GraphNode.repository_id == repo_id))
    nodes = node_result.scalars().all()

    try:
        import networkx as nx
    except ImportError:
        raise HTTPException(
            status_code=501, detail="networkx not available for path queries"
        ) from None

    graph: nx.DiGraph = nx.DiGraph()
    for e in edges:
        graph.add_edge(e.source_node_id, e.target_node_id)

    if source not in graph:
        raise HTTPException(status_code=404, detail=f"Source node '{source}' not found in graph")
    if target not in graph:
        raise HTTPException(status_code=404, detail=f"Target node '{target}' not found in graph")

    try:
        path = nx.shortest_path(graph, source, target)
    except nx.NetworkXNoPath:
        from repowise.server.mcp_server import _build_visual_context

        return {
            "path": [],
            "distance": -1,
            "explanation": "No direct dependency path found",
            "visual_context": _build_visual_context(graph, source, target, nodes, nx),
        }

    return {
        "path": path,
        "distance": len(path) - 1,
        "explanation": f"Shortest path from {source} to {target} has {len(path) - 1} hops",
    }
