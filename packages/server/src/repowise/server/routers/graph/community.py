"""Community-level views: architecture super-graph and community summaries."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.server.deps import get_db_session
from repowise.server.mcp_server._graph_utils import community_cohesion, community_label
from repowise.server.routers.graph._common import _edge_response, with_repo
from repowise.server.routers.graph.signals import (
    _EMPTY_SIGNALS,
    _collect_node_signals,
    _node_to_response,
)
from repowise.server.schemas import (
    ArchitectureEdgeResponse,
    ArchitectureGraphResponse,
    ArchitectureNodeResponse,
    CommunityDetailResponse,
    CommunityMember,
    CommunitySliceNodeResponse,
    CommunitySliceResponse,
    CommunitySummaryItem,
    NeighboringCommunity,
)

# Cap on slice member nodes. Communities are small relative to the repo, but a
# few mega-clusters exist; this keeps the blossom payload in the 50-300 target
# band and the satellite layout responsive.
_SLICE_MEMBER_CAP = 300
# Boundary stubs per slice: only the most-connected outside neighbors survive,
# so a blossom shows its strongest cross-cluster ties instead of a dust cloud.
_SLICE_BOUNDARY_CAP = 40
# Chunk size for member-id IN lists. The slice edge filter ORs two IN clauses
# (source + target) in one statement, so we cap each chunk well under SQLite's
# 999-parameter limit to leave room for both lists plus the repo_id bind.
_SLICE_IN_CHUNK = 400

router = APIRouter()


@router.get("/{repo_id}/architecture", response_model=ArchitectureGraphResponse)
async def architecture_graph(
    repo_id: str,
    min_members: int = Query(
        2, ge=1, description="Drop communities smaller than this from the view."
    ),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    _repo: object = Depends(with_repo),
) -> ArchitectureGraphResponse:
    """High-level architecture view: one node per detected community.

    Edges between communities are weighted by the number of underlying file
    edges that cross the boundary. Each super-node also carries signal counts
    (hotspots, dead files, decisions, doc coverage) so the architecture view
    surfaces health at a glance.
    """
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


@router.get(
    "/{repo_id}/communities/{community_id}/slice",
    response_model=CommunitySliceResponse,
)
async def community_slice(
    repo_id: str,
    community_id: int,
    member_limit: int = Query(_SLICE_MEMBER_CAP, ge=1, le=600),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    _repo: object = Depends(with_repo),
) -> CommunitySliceResponse:
    """Return a single community's sub-graph for the constellation blossom.

    Payload = the community's member file nodes, the edges among them, and a
    thin ring of one-hop *boundary stubs*: neighbor nodes outside the community
    that share an edge with a member, returned as minimal nodes flagged
    ``is_boundary=true`` so cross-cluster edges can render without dragging the
    whole neighbor cluster in. Sized to ~50-300 nodes.
    """
    members = await crud.get_community_members(
        session, repo_id, community_id, node_type="file", limit=member_limit + 1
    )
    truncated = len(members) > member_limit
    if truncated:
        members = members[:member_limit]
    member_ids = {m.node_id for m in members}

    # Edges touching any member: among-members stay; member<->outside become
    # cross-cluster links that pull in a boundary stub for the outside endpoint.
    # The membership filter is pushed into SQL (source OR target in members) so
    # we never load the whole repo's edge table into Python; the IN lists are
    # chunked under SQLite's parameter limit. The Python filter below is kept
    # as-is for correctness — the SQL only bounds the rows fetched (a
    # superset-or-equal of what the Python filter keeps).
    member_id_list = list(member_ids)
    edge_rows: dict[str, GraphEdge] = {}
    for start in range(0, len(member_id_list), _SLICE_IN_CHUNK):
        chunk = member_id_list[start : start + _SLICE_IN_CHUNK]
        chunk_result = await session.execute(
            select(GraphEdge).where(
                GraphEdge.repository_id == repo_id,
                or_(
                    GraphEdge.source_node_id.in_(chunk),
                    GraphEdge.target_node_id.in_(chunk),
                ),
            )
        )
        # Dedup across chunks: an edge whose endpoints fall in different chunks
        # matches twice. The PK keeps each edge once.
        for e in chunk_result.scalars():
            edge_rows[e.id] = e

    kept_edges: list[GraphEdge] = []
    boundary_degree: dict[str, int] = {}
    for e in edge_rows.values():
        src_in = e.source_node_id in member_ids
        tgt_in = e.target_node_id in member_ids
        if not src_in and not tgt_in:
            continue
        kept_edges.append(e)
        if not src_in:
            boundary_degree[e.source_node_id] = boundary_degree.get(e.source_node_id, 0) + 1
        if not tgt_in:
            boundary_degree[e.target_node_id] = boundary_degree.get(e.target_node_id, 0) + 1

    # Cap boundary stubs to the most-connected neighbors: hub communities can
    # touch thousands of outside files, which would flood the blossom with
    # dust. Edges to dropped stubs are filtered by the visible_ids pass below.
    boundary_ids = set(
        sorted(boundary_degree, key=lambda n: (-boundary_degree[n], n))[:_SLICE_BOUNDARY_CAP]
    )

    # Resolve boundary stub rows (minimal: just need a node row to render).
    boundary_nodes: list[GraphNode] = []
    if boundary_ids:
        stub_result = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repo_id,
                GraphNode.node_id.in_(boundary_ids),
            )
        )
        boundary_nodes = list(stub_result.scalars().all())
    resolved_boundary = {n.node_id for n in boundary_nodes}

    # Drop edges whose outside endpoint has no resolvable node (orphan ref).
    visible_ids = member_ids | resolved_boundary
    links = [
        _edge_response(e)
        for e in kept_edges
        if e.source_node_id in visible_ids and e.target_node_id in visible_ids
    ]

    # Member signals only (boundary stubs stay minimal / all-false signals).
    signals = await _collect_node_signals(session, repo_id, list(member_ids))
    nodes = [
        _node_to_response(m, signals.get(m.node_id, _EMPTY_SIGNALS), CommunitySliceNodeResponse)
        for m in members
    ]
    nodes.extend(
        _node_to_response(n, _EMPTY_SIGNALS, CommunitySliceNodeResponse, is_boundary=True)
        for n in boundary_nodes
    )

    return CommunitySliceResponse(
        nodes=nodes,
        links=links,
        community_id=community_id,
        member_count=len(members),
        truncated=truncated,
    )


@router.get("/{repo_id}/communities", response_model=list[CommunitySummaryItem])
async def list_communities(
    repo_id: str,
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    _repo: object = Depends(with_repo),
) -> list[CommunitySummaryItem]:
    """Return top communities by member count with labels and cohesion scores."""
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
    _repo: object = Depends(with_repo),
) -> CommunityDetailResponse:
    """Return detailed info for a single community."""
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
