"""Community-level views: architecture super-graph and community summaries.

The architecture and slice endpoints are thin wrappers over
:mod:`repowise.server.services.graph_views` so non-HTTP consumers can build
the same payloads without FastAPI.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, Query
from repowise.core.persistence import crud
from repowise.core.persistence.models import GraphNode
from repowise.server.deps import get_db_session
from repowise.server.mcp_server._graph_utils import community_cohesion, community_label
from repowise.server.routers.graph._common import with_repo
from repowise.server.schemas import (
    ArchitectureGraphResponse,
    CommunityDetailResponse,
    CommunityMember,
    CommunitySliceResponse,
    CommunitySummaryItem,
    NeighboringCommunity,
)
from repowise.server.services.graph_views import (
    SLICE_MEMBER_CAP,
    build_architecture_graph,
    build_community_slice,
)

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
    """High-level architecture view: one node per detected community."""
    return await build_architecture_graph(session, repo_id, min_members=min_members)


@router.get(
    "/{repo_id}/communities/{community_id}/slice",
    response_model=CommunitySliceResponse,
)
async def community_slice(
    repo_id: str,
    community_id: int,
    member_limit: int = Query(SLICE_MEMBER_CAP, ge=1, le=600),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    _repo: object = Depends(with_repo),
) -> CommunitySliceResponse:
    """Return a single community's sub-graph for the constellation blossom."""
    return await build_community_slice(
        session, repo_id, community_id, member_limit=member_limit
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
