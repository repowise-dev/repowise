"""Community-level views: architecture super-graph and community summaries.

The architecture and slice endpoints are thin wrappers over
:mod:`repowise.server.services.graph_views` so non-HTTP consumers can build
the same payloads without FastAPI.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

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
from repowise.server.services.node_signals import EMPTY_SIGNALS, collect_node_signals

# How many members the detail endpoint reads to compute the community's shape
# and state. It used to be 200, which silently disagreed with the community
# *list* — a 587-file community reported 587 there and 200 here, and every
# rollup below covered a third of it.
#
# Ceiling: a community larger than this still reports this number, and its
# counts still cover only the top slice by PageRank. The largest community in
# any repo indexed so far is under 600, so nothing reaches it. If one does, the
# upgrade is a `SELECT COUNT(*)` for the true `member_count` plus an explicit
# "counted over the top N" field, not a bigger constant.
_MEMBER_READ_CAP = 2000

router = APIRouter()


@router.get("/{repo_id}/architecture", response_model=ArchitectureGraphResponse)
async def architecture_graph(
    repo_id: str,
    min_members: int = Query(
        2, ge=1, description="Drop communities smaller than this from the view."
    ),
    session: AsyncSession = Depends(get_db_session),
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
    session: AsyncSession = Depends(get_db_session),
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
    session: AsyncSession = Depends(get_db_session),
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
    session: AsyncSession = Depends(get_db_session),
    _repo: object = Depends(with_repo),
) -> CommunityDetailResponse:
    """Return detailed info for a single community."""
    all_members = await crud.get_community_members(
        session, repo_id, community_id, node_type="file", limit=_MEMBER_READ_CAP
    )
    if not all_members:
        raise HTTPException(status_code=404, detail="Community not found or empty")

    top = max(all_members, key=lambda m: m.pagerank or 0.0)
    label = community_label(top)
    cohesion = community_cohesion(top)

    # State of the area, in two path-scoped reads over the same member list the
    # shape figures above are computed from. `collect_node_signals` is the same
    # join `build_architecture_graph` uses for the hub discs, so a community's
    # hot/dead counts here and its disc on the canvas can never disagree.
    member_paths = [m.node_id for m in all_members]
    signals = await collect_node_signals(session, repo_id, member_paths)
    hot_count = sum(1 for p in member_paths if signals.get(p, EMPTY_SIGNALS).is_hotspot)
    dead_count = sum(1 for p in member_paths if signals.get(p, EMPTY_SIGNALS).is_dead)
    decision_count = sum(
        1 for p in member_paths if signals.get(p, EMPTY_SIGNALS).has_decision
    )

    # Most files owned, not most commits: ownership is denormalised onto
    # GitMetadata.primary_owner_name and there is no per-(file, author) table to
    # tally properly. The field name and the panel copy both say "owns".
    owner_files: dict[str, int] = {}
    for p in member_paths:
        owner = signals.get(p, EMPTY_SIGNALS).primary_owner
        if owner:
            owner_files[owner] = owner_files.get(owner, 0) + 1
    primary_owner: str | None = None
    primary_owner_file_count = 0
    if owner_files:
        primary_owner, primary_owner_file_count = max(
            owner_files.items(), key=lambda kv: (kv[1], kv[0])
        )

    # LOC-weighted mean over the members that carry a score, matching
    # `rollup_health`: effective score prefers the split defect_score and falls
    # back to the overall score, weight is max(nloc, 1), and a community where
    # nothing is scored gets None rather than a zero it never measured.
    health_metrics = await crud.get_health_metrics(
        session, repo_id, file_paths=member_paths
    )
    weighted_sum = 0.0
    weight = 0.0
    scored_member_count = 0
    for hm in health_metrics:
        score = hm.defect_score if hm.defect_score is not None else hm.score
        if score is None:
            continue
        w = float(max(hm.nloc or 1, 1))
        weighted_sum += score * w
        weight += w
        scored_member_count += 1
    health_score = round(weighted_sum / weight, 2) if weight > 0 else None

    members_out: list[CommunityMember] = []
    if include_members:
        for m in all_members[:member_limit]:
            sig = signals.get(m.node_id, EMPTY_SIGNALS)
            members_out.append(
                CommunityMember(
                    path=m.node_id,
                    pagerank=round(m.pagerank or 0.0, 6),
                    is_entry_point=m.is_entry_point,
                    is_hotspot=sig.is_hotspot,
                    is_dead=sig.is_dead,
                )
            )

    # Neighboring communities. `get_cross_community_edges` orders by edge count
    # descending, so the top slice is taken *before* the label loop: it used to
    # resolve a label for every neighbour in the repo and then keep ten.
    cross_edges = (await crud.get_cross_community_edges(session, repo_id, community_id))[
        :10
    ]
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
        for ce in cross_edges
    ]

    return CommunityDetailResponse(
        community_id=community_id,
        label=label,
        cohesion=cohesion,
        member_count=len(all_members),
        members=members_out,
        truncated=len(all_members) > member_limit,
        neighboring_communities=neighbors,
        health_score=health_score,
        scored_member_count=scored_member_count,
        hot_count=hot_count,
        dead_count=dead_count,
        decision_count=decision_count,
        primary_owner=primary_owner,
        primary_owner_file_count=primary_owner_file_count,
    )
