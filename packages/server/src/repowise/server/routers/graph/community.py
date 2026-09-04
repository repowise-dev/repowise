"""Community-level views: architecture super-graph and community summaries.

The architecture and slice endpoints are thin wrappers over
:mod:`repowise.server.services.graph_views` so non-HTTP consumers can build
the same payloads without FastAPI.

Every endpoint takes the same three population flags (all off), so the map,
the list, the panel and the drill-down count the same files.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.server.deps import get_db_session
from repowise.server.mcp_server._graph_utils import (
    community_cohesion,
    community_conductance,
    community_label,
)
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
    MEMBER_READ_CAP,
    SLICE_MEMBER_CAP,
    Population,
    bucket_by_community,
    build_architecture_graph,
    build_community_slice,
    neighbour_edge_counts,
    split_population,
)
from repowise.server.services.node_signals import EMPTY_SIGNALS, collect_node_signals

router = APIRouter()


def population_params(
    include_tests: bool = Query(
        False, description="Count test files as members. Off: production only."
    ),
    include_examples: bool = Query(
        False, description="Count example, demo and benchmark files as members."
    ),
    include_docs: bool = Query(
        False, description="Count documentation and configuration files as members."
    ),
) -> Population:
    """The population flags, shared by every community endpoint."""
    return Population(
        include_tests=include_tests,
        include_examples=include_examples,
        include_docs=include_docs,
    )


@router.get("/{repo_id}/architecture", response_model=ArchitectureGraphResponse)
async def architecture_graph(
    repo_id: str,
    min_members: int = Query(
        2, ge=1, description="Drop communities smaller than this from the view."
    ),
    population: Population = Depends(population_params),
    session: AsyncSession = Depends(get_db_session),
    _repo: object = Depends(with_repo),
) -> ArchitectureGraphResponse:
    """High-level architecture view: one node per detected community."""
    return await build_architecture_graph(
        session, repo_id, min_members=min_members, population=population
    )


@router.get(
    "/{repo_id}/communities/{community_id}/slice",
    response_model=CommunitySliceResponse,
)
async def community_slice(
    repo_id: str,
    community_id: int,
    member_limit: int = Query(SLICE_MEMBER_CAP, ge=1, le=600),
    population: Population = Depends(population_params),
    session: AsyncSession = Depends(get_db_session),
    _repo: object = Depends(with_repo),
) -> CommunitySliceResponse:
    """Return a single community's sub-graph for the drill-down."""
    return await build_community_slice(
        session, repo_id, community_id, member_limit=member_limit, population=population
    )


@router.get("/{repo_id}/communities", response_model=list[CommunitySummaryItem])
async def list_communities(
    repo_id: str,
    limit: int = Query(20, ge=1, le=100),
    population: Population = Depends(population_params),
    session: AsyncSession = Depends(get_db_session),
    _repo: object = Depends(with_repo),
) -> list[CommunitySummaryItem]:
    """Return top communities by visible member count with labels and shape scores."""
    all_nodes = await crud.get_all_file_metrics(session, repo_id)
    visible, _counts = split_population(all_nodes, population)
    buckets, hidden = bucket_by_community(all_nodes, visible)

    items: list[CommunitySummaryItem] = []
    for cid, members in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        # Pick top-pagerank member for label/cohesion extraction
        top = max(members, key=lambda m: m.pagerank or 0.0)
        items.append(
            CommunitySummaryItem(
                community_id=cid,
                label=community_label(top),
                cohesion=community_cohesion(top),
                conductance=community_conductance(top),
                member_count=len(members),
                hidden_member_count=hidden[cid],
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
    population: Population = Depends(population_params),
    session: AsyncSession = Depends(get_db_session),
    _repo: object = Depends(with_repo),
) -> CommunityDetailResponse:
    """Return detailed info for a single community."""
    all_members = await crud.get_community_members(
        session, repo_id, community_id, node_type="file", limit=MEMBER_READ_CAP
    )
    if not all_members:
        raise HTTPException(status_code=404, detail="Community not found or empty")

    # Label and shape are stored on every member; the counts below are over the
    # visible members only. An all-hidden group reports zero, not 404.
    top = max(all_members, key=lambda m: m.pagerank or 0.0)
    label = community_label(top)
    cohesion = community_cohesion(top)
    conductance = community_conductance(top)

    members, counts = split_population(all_members, population)
    hidden_member_count = sum(counts.values()) - len(members)

    # State of the area, in two path-scoped reads over the same member list the
    # shape figures above are computed from. `collect_node_signals` is the same
    # join `build_architecture_graph` uses for the hub discs, so a community's
    # hot/dead counts here and its disc on the canvas can never disagree.
    member_paths = [m.node_id for m in members]
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
    health_metrics = (
        await crud.get_health_metrics(session, repo_id, file_paths=member_paths)
        if member_paths
        else []
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
        for m in members[:member_limit]:
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

    # Same population on both ends as the map's cross edges; sliced before the
    # label loop.
    cross_edges = (
        await neighbour_edge_counts(
            session, repo_id, community_id, member_paths, population=population
        )
    )[:10]
    neighbor_labels: dict[int, str] = {}
    for ncid, _count in cross_edges:
        nbr_members = await crud.get_community_members(
            session, repo_id, ncid, node_type="file", limit=1
        )
        if nbr_members:
            neighbor_labels[ncid] = community_label(nbr_members[0])
        else:
            neighbor_labels[ncid] = f"cluster_{ncid}"

    neighbors = [
        NeighboringCommunity(
            community_id=ncid,
            label=neighbor_labels.get(ncid, ""),
            cross_edge_count=count,
        )
        for ncid, count in cross_edges
    ]

    return CommunityDetailResponse(
        community_id=community_id,
        label=label,
        cohesion=cohesion,
        conductance=conductance,
        member_count=len(members),
        hidden_member_count=hidden_member_count,
        members=members_out,
        truncated=len(members) > member_limit,
        neighboring_communities=neighbors,
        health_score=health_score,
        scored_member_count=scored_member_count,
        hot_count=hot_count,
        dead_count=dead_count,
        decision_count=decision_count,
        primary_owner=primary_owner,
        primary_owner_file_count=primary_owner_file_count,
    )
