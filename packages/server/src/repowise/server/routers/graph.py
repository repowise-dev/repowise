"""/api/graph — Dependency graph export in D3-compatible format."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, Query
from repowise.core.persistence import crud
from repowise.core.persistence.models import (
    DeadCodeFinding,
    DecisionRecord,
    GitMetadata,
    GraphEdge,
    GraphNode,
    Page,
)
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.mcp_server._graph_utils import (
    bfs_trace,
    community_cohesion,
    community_label,
    entry_point_score as _ep_score,
    parse_community_meta,
    percentile_rank,
    resolve_trace_communities,
)
from repowise.server.schemas import (
    ArchitectureEdgeResponse,
    ArchitectureGraphResponse,
    ArchitectureNodeResponse,
    CallerCalleeEntry,
    CallersCalleesResponse,
    CommunityDetailResponse,
    CommunityMember,
    CommunitySummaryItem,
    DeadCodeGraphNodeResponse,
    DeadCodeGraphResponse,
    EgoGraphResponse,
    ExecutionFlowEntry,
    ExecutionFlowsResponse,
    GitMetadataResponse,
    GraphEdgeResponse,
    GraphExportResponse,
    GraphMetricsResponse,
    GraphNodeResponse,
    HotFilesGraphResponse,
    HotFilesNodeResponse,
    ModuleEdgeResponse,
    ModuleGraphResponse,
    ModuleNodeResponse,
    NeighboringCommunity,
    NodeSearchResult,
    SymbolNodeSummary,
)

# Cap on full-graph export; above this we return top-N by PageRank with truncated=True.
# Sized to fit a modern client-side force layout without hitching.
_FULL_GRAPH_NODE_CAP = 5000

router = APIRouter(
    prefix="/api/graph",
    tags=["graph"],
    dependencies=[Depends(verify_api_key)],
)


def _escape_like(s: str) -> str:
    """Escape special characters for SQL LIKE patterns."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
# Cross-link signal collection — single reusable join layer
# ---------------------------------------------------------------------------


class NodeSignals:
    """Per-node cross-link signals (git, dead-code, decisions, docs)."""

    __slots__ = (
        "is_hotspot",
        "churn_percentile",
        "is_dead",
        "dead_confidence",
        "has_decision",
        "primary_owner",
        "has_doc",
    )

    def __init__(self) -> None:
        self.is_hotspot: bool = False
        self.churn_percentile: float | None = None
        self.is_dead: bool = False
        self.dead_confidence: float | None = None
        self.has_decision: bool = False
        self.primary_owner: str | None = None
        self.has_doc: bool = False


_EMPTY_SIGNALS = NodeSignals()


async def _collect_node_signals(
    session: AsyncSession,
    repo_id: str,
    node_ids: list[str] | None = None,
) -> dict[str, NodeSignals]:
    """Join git metadata, dead-code findings, decisions, and docs into one map.

    When *node_ids* is provided, queries are scoped to that subset; otherwise
    we fetch repo-wide. Returns ``{node_id: NodeSignals}``. Missing keys imply
    default (all-false) signals.
    """
    signals: dict[str, NodeSignals] = {}

    def _entry(path: str) -> NodeSignals:
        existing = signals.get(path)
        if existing is None:
            existing = NodeSignals()
            signals[path] = existing
        return existing

    # Git metadata: hotspot + churn + owner
    git_q = select(GitMetadata).where(GitMetadata.repository_id == repo_id)
    if node_ids is not None:
        git_q = git_q.where(GitMetadata.file_path.in_(node_ids))
    for gm in (await session.execute(git_q)).scalars():
        s = _entry(gm.file_path)
        s.is_hotspot = bool(gm.is_hotspot)
        s.churn_percentile = gm.churn_percentile
        s.primary_owner = gm.primary_owner_name

    # Dead-code findings (only open, unreachable_file)
    dead_q = select(DeadCodeFinding).where(
        DeadCodeFinding.repository_id == repo_id,
        DeadCodeFinding.status == "open",
        DeadCodeFinding.kind == "unreachable_file",
    )
    if node_ids is not None:
        dead_q = dead_q.where(DeadCodeFinding.file_path.in_(node_ids))
    for f in (await session.execute(dead_q)).scalars():
        s = _entry(f.file_path)
        s.is_dead = True
        s.dead_confidence = f.confidence

    # Decisions: each decision lists affected files in JSON
    dec_q = select(DecisionRecord.affected_files_json).where(
        DecisionRecord.repository_id == repo_id,
        DecisionRecord.status.in_(("active", "proposed")),
    )
    decision_paths: set[str] = set()
    for (raw,) in (await session.execute(dec_q)).all():
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, list):
            for p in parsed:
                if isinstance(p, str):
                    decision_paths.add(p)
    if node_ids is not None:
        decision_paths &= set(node_ids)
    for p in decision_paths:
        _entry(p).has_decision = True

    # Docs (Page.target_path)
    doc_q = select(Page.target_path).where(Page.repository_id == repo_id)
    if node_ids is not None:
        doc_q = doc_q.where(Page.target_path.in_(node_ids))
    for (path,) in (await session.execute(doc_q)).all():
        if path:
            _entry(path).has_doc = True

    return signals


def _to_graph_node(n: GraphNode, signals: NodeSignals) -> GraphNodeResponse:
    """Build a GraphNodeResponse from a GraphNode + its collected signals."""
    return GraphNodeResponse(
        node_id=n.node_id,
        node_type=n.node_type,
        language=n.language,
        symbol_count=n.symbol_count,
        pagerank=n.pagerank,
        betweenness=n.betweenness,
        community_id=n.community_id,
        is_test=n.is_test,
        is_entry_point=n.is_entry_point,
        has_doc=signals.has_doc,
        is_hotspot=signals.is_hotspot,
        churn_percentile=signals.churn_percentile,
        is_dead=signals.is_dead,
        dead_confidence=signals.dead_confidence,
        has_decision=signals.has_decision,
        primary_owner=signals.primary_owner,
    )


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

    # Group nodes by first path segment, detecting monorepo roots
    first_level: dict[str, list[GraphNode]] = {}
    for n in nodes:
        parts = n.node_id.split("/")
        seg = parts[0] if len(parts) > 1 else n.node_id
        first_level.setdefault(seg, []).append(n)

    total = len(nodes) or 1
    monorepo_roots: set[str] = set()
    for seg, seg_nodes in first_level.items():
        # When a single top-level directory contains >70% of all files, treat it
        # as a monorepo root and group by the *second* path segment instead.
        # Threshold chosen empirically: catches src/-heavy repos without
        # splitting balanced monorepos like packages/a + packages/b (each ~50%).
        if len(seg_nodes) / total > 0.70:
            monorepo_roots.add(seg)

    modules: dict[str, list[GraphNode]] = {}
    for n in nodes:
        parts = n.node_id.split("/")
        if len(parts) <= 1:
            module = n.node_id
        elif parts[0] in monorepo_roots and len(parts) > 2:
            module = parts[0] + "/" + parts[1]
        else:
            module = parts[0]
        modules.setdefault(module, []).append(n)

    # Fetch page confidence for doc coverage
    page_result = await session.execute(
        select(Page.target_path, Page.confidence).where(Page.repository_id == repo_id)
    )
    page_coverage: dict[str, float] = {row.target_path: row.confidence for row in page_result.all()}

    # Collect signals once for all file nodes; aggregate per module below.
    all_node_ids = [n.node_id for n in nodes]
    signals = await _collect_node_signals(session, repo_id, all_node_ids)

    node_to_module: dict[str, str] = {}
    module_nodes: list[ModuleNodeResponse] = []
    for module_id, module_file_nodes in modules.items():
        file_count = len(module_file_nodes)
        symbol_count = sum(n.symbol_count for n in module_file_nodes)
        avg_pagerank = sum(n.pagerank for n in module_file_nodes) / max(file_count, 1)
        covered = sum(1 for n in module_file_nodes if page_coverage.get(n.node_id, 0.0) >= 0.7)
        doc_coverage_pct = covered / max(file_count, 1)

        hotspot_count = 0
        dead_count = 0
        has_decision = False
        owner_tally: dict[str, int] = {}
        for n in module_file_nodes:
            sig = signals.get(n.node_id, _EMPTY_SIGNALS)
            if sig.is_hotspot:
                hotspot_count += 1
            if sig.is_dead:
                dead_count += 1
            if sig.has_decision:
                has_decision = True
            if sig.primary_owner:
                owner_tally[sig.primary_owner] = owner_tally.get(sig.primary_owner, 0) + 1
        primary_owner = max(owner_tally, key=owner_tally.get) if owner_tally else None

        for n in module_file_nodes:
            node_to_module[n.node_id] = module_id

        module_nodes.append(
            ModuleNodeResponse(
                module_id=module_id,
                file_count=file_count,
                symbol_count=symbol_count,
                avg_pagerank=avg_pagerank,
                doc_coverage_pct=doc_coverage_pct,
                hotspot_count=hotspot_count,
                dead_count=dead_count,
                has_decision=has_decision,
                primary_owner=primary_owner,
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

    signals = await _collect_node_signals(session, repo_id, list(reachable))

    node_responses = [_to_graph_node(n, signals.get(n.node_id, _EMPTY_SIGNALS)) for n in nodes]

    link_responses = [
        GraphEdgeResponse(
            source=e.source_node_id,
            target=e.target_node_id,
            imported_names=_parse_imported_names(e.imported_names_json),
        )
        for e in edges
        if e.source_node_id in reachable and e.target_node_id in reachable
    ]

    return GraphExportResponse(
        nodes=node_responses,
        links=link_responses,
        total_node_count=len(node_responses),
    )


# ---------------------------------------------------------------------------
# Architecture graph — community super-nodes
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/architecture", response_model=ArchitectureGraphResponse)
async def architecture_graph(
    repo_id: str,
    min_members: int = Query(
        2, ge=1, description="Drop communities smaller than this from the view."
    ),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ArchitectureGraphResponse:
    """High-level architecture view: one node per detected community.

    Edges between communities are weighted by the number of underlying file
    edges that cross the boundary. Each super-node also carries signal counts
    (hotspots, dead files, decisions, doc coverage) so the architecture view
    surfaces health at a glance.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    all_nodes = await crud.get_all_file_metrics(session, repo_id)
    if not all_nodes:
        return ArchitectureGraphResponse(nodes=[], edges=[])

    # Group file nodes by community
    buckets: dict[int, list[GraphNode]] = {}
    node_to_community: dict[str, int] = {}
    for n in all_nodes:
        cid = n.community_id if n.community_id is not None else 0
        buckets.setdefault(cid, []).append(n)
        node_to_community[n.node_id] = cid

    # Pull cross-link signals once for the whole repo so super-nodes can
    # aggregate hotspot/dead/decision counts without N round-trips.
    signals = await _collect_node_signals(session, repo_id, [n.node_id for n in all_nodes])

    arch_nodes: list[ArchitectureNodeResponse] = []
    for cid, members in buckets.items():
        if len(members) < min_members:
            continue
        top = max(members, key=lambda m: m.pagerank or 0.0)
        hotspot_count = 0
        dead_count = 0
        has_decision = False
        doc_hits = 0
        langs: dict[str, int] = {}
        for m in members:
            sig = signals.get(m.node_id, _EMPTY_SIGNALS)
            if sig.is_hotspot:
                hotspot_count += 1
            if sig.is_dead:
                dead_count += 1
            if sig.has_decision:
                has_decision = True
            if sig.has_doc:
                doc_hits += 1
            if m.language:
                langs[m.language] = langs.get(m.language, 0) + 1
        top_langs = [lang for lang, _ in sorted(langs.items(), key=lambda kv: -kv[1])[:3]]
        avg_pr = sum(m.pagerank or 0.0 for m in members) / max(len(members), 1)

        arch_nodes.append(
            ArchitectureNodeResponse(
                community_id=cid,
                label=community_label(top),
                cohesion=community_cohesion(top),
                member_count=len(members),
                top_file=top.node_id,
                avg_pagerank=avg_pr,
                hotspot_count=hotspot_count,
                dead_count=dead_count,
                has_decision=has_decision,
                doc_coverage_pct=doc_hits / max(len(members), 1),
                languages=top_langs,
            )
        )

    arch_nodes.sort(key=lambda a: -a.member_count)
    kept_communities = {a.community_id for a in arch_nodes}

    # Collapse cross-community edges
    edge_result = await session.execute(select(GraphEdge).where(GraphEdge.repository_id == repo_id))
    edge_counts: dict[tuple[int, int], int] = {}
    for e in edge_result.scalars():
        src_c = node_to_community.get(e.source_node_id)
        tgt_c = node_to_community.get(e.target_node_id)
        if src_c is None or tgt_c is None or src_c == tgt_c:
            continue
        if src_c not in kept_communities or tgt_c not in kept_communities:
            continue
        key = (src_c, tgt_c)
        edge_counts[key] = edge_counts.get(key, 0) + 1

    arch_edges = [
        ArchitectureEdgeResponse(source=s, target=t, edge_count=c)
        for (s, t), c in edge_counts.items()
    ]

    return ArchitectureGraphResponse(nodes=arch_nodes, edges=arch_edges)


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
        sig = signals.get(n.node_id, _EMPTY_SIGNALS)
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
            has_doc=sig.has_doc,
            is_hotspot=sig.is_hotspot,
            churn_percentile=sig.churn_percentile,
            is_dead=sig.is_dead,
            dead_confidence=sig.dead_confidence,
            has_decision=sig.has_decision,
            primary_owner=sig.primary_owner,
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
    signals = await _collect_node_signals(session, repo_id, list(all_node_ids))

    def _to_hot_node(n: GraphNode, commit_count: int) -> HotFilesNodeResponse:
        sig = signals.get(n.node_id, _EMPTY_SIGNALS)
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
            has_doc=sig.has_doc,
            is_hotspot=sig.is_hotspot,
            churn_percentile=sig.churn_percentile,
            is_dead=sig.is_dead,
            dead_confidence=sig.dead_confidence,
            has_decision=sig.has_decision,
            primary_owner=sig.primary_owner,
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
            GraphNode.node_id.ilike(f"%{_escape_like(q)}%"),
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
    limit: int = Query(
        _FULL_GRAPH_NODE_CAP,
        ge=1,
        le=100_000,
        description="Maximum nodes to return (top-N by PageRank). Set very high to fetch all.",
    ),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> GraphExportResponse:
    """Export the full dependency graph in D3 force-directed format.

    Large repos are capped to top-N nodes by PageRank with ``truncated=True``;
    clients should surface a banner and let the user request unrestricted load.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    node_result = await session.execute(
        select(GraphNode)
        .where(GraphNode.repository_id == repo_id)
        .order_by(GraphNode.pagerank.desc())
    )
    all_nodes = node_result.scalars().all()
    total_node_count = len(all_nodes)
    truncated = total_node_count > limit
    nodes = all_nodes[:limit] if truncated else all_nodes
    kept_ids = {n.node_id for n in nodes}

    edge_result = await session.execute(select(GraphEdge).where(GraphEdge.repository_id == repo_id))
    edges = edge_result.scalars().all()

    signals = await _collect_node_signals(session, repo_id, list(kept_ids) if truncated else None)

    node_responses = [_to_graph_node(n, signals.get(n.node_id, _EMPTY_SIGNALS)) for n in nodes]

    link_responses = [
        GraphEdgeResponse(
            source=e.source_node_id,
            target=e.target_node_id,
            imported_names=_parse_imported_names(e.imported_names_json),
        )
        for e in edges
        if e.source_node_id in kept_ids and e.target_node_id in kept_ids
    ]

    return GraphExportResponse(
        nodes=node_responses,
        links=link_responses,
        truncated=truncated,
        total_node_count=total_node_count,
    )


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


# ---------------------------------------------------------------------------
# Communities
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/communities", response_model=list[CommunitySummaryItem])
async def list_communities(
    repo_id: str,
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[CommunitySummaryItem]:
    """Return top communities by member count with labels and cohesion scores."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    all_nodes = await crud.get_all_file_metrics(session, repo_id)

    # Group by community_id
    buckets: dict[int, list[GraphNode]] = {}
    for n in all_nodes:
        cid = n.community_id if n.community_id is not None else 0
        buckets.setdefault(cid, []).append(n)

    items: list[CommunitySummaryItem] = []
    for cid, members in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        # Pick top-pagerank member for label/cohesion extraction
        top = max(members, key=lambda m: m.pagerank or 0.0)
        items.append(
            CommunitySummaryItem(
                community_id=cid,
                label=community_label(top),
                cohesion=community_cohesion(top),
                member_count=len(members),
                top_file=top.node_id,
            )
        )
        if len(items) >= limit:
            break

    return items


@router.get(
    "/{repo_id}/communities/{community_id}",
    response_model=CommunityDetailResponse,
)
async def get_community_detail(
    repo_id: str,
    community_id: int,
    include_members: bool = Query(True),
    member_limit: int = Query(30, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CommunityDetailResponse:
    """Return detailed info for a single community."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    all_members = await crud.get_community_members(
        session, repo_id, community_id, node_type="file", limit=200
    )
    if not all_members:
        raise HTTPException(status_code=404, detail="Community not found or empty")

    top = max(all_members, key=lambda m: m.pagerank or 0.0)
    label = community_label(top)
    cohesion = community_cohesion(top)

    members_out: list[CommunityMember] = []
    if include_members:
        for m in all_members[:member_limit]:
            members_out.append(
                CommunityMember(
                    path=m.node_id,
                    pagerank=round(m.pagerank or 0.0, 6),
                    is_entry_point=m.is_entry_point,
                )
            )

    # Neighboring communities
    cross_edges = await crud.get_cross_community_edges(session, repo_id, community_id)
    # Resolve labels for neighbors
    neighbor_cids = [ce["target_community_id"] for ce in cross_edges]
    neighbor_labels: dict[int, str] = {}
    for ncid in neighbor_cids:
        nbr_members = await crud.get_community_members(
            session, repo_id, ncid, node_type="file", limit=1
        )
        if nbr_members:
            neighbor_labels[ncid] = community_label(nbr_members[0])
        else:
            neighbor_labels[ncid] = f"cluster_{ncid}"

    neighbors = [
        NeighboringCommunity(
            community_id=ce["target_community_id"],
            label=neighbor_labels.get(ce["target_community_id"], ""),
            cross_edge_count=ce["edge_count"],
        )
        for ce in cross_edges[:10]
    ]

    return CommunityDetailResponse(
        community_id=community_id,
        label=label,
        cohesion=cohesion,
        member_count=len(all_members),
        members=members_out,
        truncated=len(all_members) > member_limit,
        neighboring_communities=neighbors,
    )


# ---------------------------------------------------------------------------
# Graph Metrics
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/metrics", response_model=GraphMetricsResponse)
async def get_graph_metrics(
    repo_id: str,
    node_id: str = Query(..., description="File path or symbol_id"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> GraphMetricsResponse:
    """Return importance metrics for a file or symbol with percentile ranks."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

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


# ---------------------------------------------------------------------------
# Callers / Callees
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/callers-callees", response_model=CallersCalleesResponse)
async def get_callers_callees(
    repo_id: str,
    symbol_id: str = Query(..., description="Symbol node ID (path::Name)"),
    direction: str = Query("both", description="callers, callees, or both"),
    edge_types: str = Query("calls", description="Comma-separated edge types"),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CallersCalleesResponse:
    """Find who calls a symbol and what it calls. Also works for class hierarchy."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

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
        session, repo_id, node.node_id,
        direction=direction, edge_types=et_list, limit=limit,
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
            name=other.name if other else (other_id.split("::")[-1] if "::" in other_id else other_id),
            kind=other.kind if other else "unknown",
            file=other.file_path if other else (other_id.split("::")[0] if "::" in other_id else other_id),
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


# ---------------------------------------------------------------------------
# Execution Flows
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/execution-flows", response_model=ExecutionFlowsResponse)
async def get_execution_flows(
    repo_id: str,
    top_n: int = Query(5, ge=1, le=20),
    max_depth: int = Query(5, ge=1, le=12),
    entry_point: str | None = Query(None, description="Specific symbol to trace from"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ExecutionFlowsResponse:
    """Return top entry points with BFS call-path traces."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    entry_nodes: list[tuple[GraphNode, float]] = []

    if entry_point:
        node = await crud.get_graph_node(session, repo_id, entry_point)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Entry point not found: {entry_point}")
        entry_nodes = [(node, _ep_score(node))]
    else:
        top_nodes = await crud.get_top_entry_points(
            session, repo_id, min_score=0.0, limit=top_n
        )
        for n in top_nodes:
            entry_nodes.append((n, _ep_score(n)))

    if not entry_nodes:
        return ExecutionFlowsResponse(total_entry_points=0, flows=[])

    node_cache: dict[str, GraphNode] = {}
    flows: list[ExecutionFlowEntry] = []

    for ep_node, ep_score in entry_nodes:
        trace = await bfs_trace(
            session, repo_id, ep_node.node_id, max_depth, node_cache
        )
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
