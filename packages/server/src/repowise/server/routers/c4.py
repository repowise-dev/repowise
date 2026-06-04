"""/api/graph/{repo_id}/c4 — C4 architecture diagram endpoints.

Three levels:
    L1  System Context — the system + people + external systems
    L2  Containers     — workspace packages + external deps + edges
    L3  Components     — sub-modules inside one container + edges

The shapes match ``server.schemas.C4L*Response`` and are derived on demand
from the persisted graph by :mod:`server.services.c4_builder`. No on-disk
work happens here, so this works on hosted backends without a checkout.
"""

from __future__ import annotations

from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, Query
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import (
    ArchEdgeResponse,
    ArchitectureViewResponse,
    ArchLayerResponse,
    ArchNodeResponse,
    ArchSubGroupResponse,
    ArchTourStepResponse,
    C4ComponentResponse,
    C4ContainerResponse,
    C4ExternalSystemResponse,
    C4L1Response,
    C4L2Response,
    C4L3Response,
    C4PersonResponse,
    C4RelationResponse,
    C4SystemResponse,
)
from repowise.server.services import c4_builder
from repowise.server.services.c4_builder.architecture import build_architecture_view
from repowise.server.services.c4_builder.mermaid import (
    to_mermaid_l1,
    to_mermaid_l2,
    to_mermaid_l3,
)
from repowise.server.services.c4_builder.models import (
    ArchEdge,
    ArchitectureView,
    ArchLayer,
    ArchNode,
    ArchTourStep,
    Component,
    Container,
    ExternalSystemView,
    Person,
    Relation,
    System,
)

router = APIRouter(
    prefix="/api/graph",
    tags=["c4"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("/{repo_id}/c4/l1", response_model=C4L1Response)
async def get_c4_l1(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> C4L1Response:
    view = await c4_builder.build_l1(session, repo_id)
    return C4L1Response(
        system=_system(view.system),
        people=[_person(p) for p in view.people],
        external_systems=[_external(e) for e in view.external_systems],
        relations=[_relation(r) for r in view.relations],
    )


@router.get("/{repo_id}/c4/l2", response_model=C4L2Response)
async def get_c4_l2(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> C4L2Response:
    view = await c4_builder.build_l2(session, repo_id)
    return C4L2Response(
        containers=[_container(c) for c in view.containers],
        external_systems=[_external(e) for e in view.external_systems],
        relations=[_relation(r) for r in view.relations],
    )


@router.get("/{repo_id}/c4/l3", response_model=C4L3Response)
async def get_c4_l3(
    repo_id: str,
    container_id: str = Query(..., description="Container id from L2 (e.g., pkg:packages/core)"),
    session: AsyncSession = Depends(get_db_session),
) -> C4L3Response:
    view = await c4_builder.build_l3(session, repo_id, container_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"container not found: {container_id}")
    return C4L3Response(
        container=_container(view.container),
        components=[_component(c) for c in view.components],
        external_systems=[_external(e) for e in view.external_systems],
        relations=[_relation(r) for r in view.relations],
    )


@router.get(
    "/{repo_id}/c4/mermaid",
    response_class=PlainTextResponse,
    responses={200: {"content": {"text/plain": {}}}},
)
async def get_c4_mermaid(
    repo_id: str,
    level: int = Query(2, ge=1, le=3, description="C4 level: 1, 2, or 3"),
    container_id: str | None = Query(None, description="Required when level=3"),
    session: AsyncSession = Depends(get_db_session),
) -> PlainTextResponse:
    """Mermaid C4 source for the requested level — paste into mermaid.live or
    embed in markdown. Same data source as the JSON endpoints, so what you
    see in the diagram view matches what you export.
    """
    if level == 1:
        view_l1 = await c4_builder.build_l1(session, repo_id)
        return PlainTextResponse(to_mermaid_l1(view_l1))

    if level == 2:
        view_l2 = await c4_builder.build_l2(session, repo_id)
        repo = await c4_builder.load_repo(session, repo_id)
        system_name = repo.name if repo is not None else repo_id
        return PlainTextResponse(to_mermaid_l2(view_l2, system_name=system_name))

    if not container_id:
        raise HTTPException(status_code=400, detail="container_id is required for level=3")
    view_l3 = await c4_builder.build_l3(session, repo_id, container_id)
    if view_l3 is None:
        raise HTTPException(status_code=404, detail=f"container not found: {container_id}")
    repo = await c4_builder.load_repo(session, repo_id)
    system_name = repo.name if repo is not None else repo_id
    return PlainTextResponse(to_mermaid_l3(view_l3, system_name=system_name))


# ---------------------------------------------------------------------------
# Dataclass → Pydantic adapters (kept tiny on purpose)
# ---------------------------------------------------------------------------


def _system(s: System) -> C4SystemResponse:
    return C4SystemResponse(id=s.id, name=s.name, description=s.description)


def _person(p: Person) -> C4PersonResponse:
    return C4PersonResponse(id=p.id, name=p.name, description=p.description)


def _external(e: ExternalSystemView) -> C4ExternalSystemResponse:
    return C4ExternalSystemResponse(
        id=e.id,
        name=e.name,
        display_name=e.display_name,
        category=e.category,
        ecosystem=e.ecosystem,
        version=e.version,
    )


def _container(c: Container) -> C4ContainerResponse:
    return C4ContainerResponse(
        id=c.id,
        name=c.name,
        path=c.path,
        language=c.language,
        file_count=c.file_count,
        symbol_count=c.symbol_count,
        hotspot_count=c.hotspot_count,
        dead_count=c.dead_count,
    )


def _component(c: Component) -> C4ComponentResponse:
    return C4ComponentResponse(
        id=c.id,
        name=c.name,
        path=c.path,
        container_id=c.container_id,
        file_count=c.file_count,
        symbol_count=c.symbol_count,
    )


def _relation(r: Relation) -> C4RelationResponse:
    return C4RelationResponse(
        source_id=r.source_id,
        target_id=r.target_id,
        label=r.label,
        edge_count=r.edge_count,
        edge_types=list(r.edge_types),
    )


# ---------------------------------------------------------------------------
# Architecture view endpoint + adapters
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/architecture-view")
async def get_architecture_view(
    repo_id: str,
    include_symbols: bool = Query(False, description="Include symbol-level nodes"),
    session: AsyncSession = Depends(get_db_session),
) -> ArchitectureViewResponse:
    view = await build_architecture_view(session, repo_id, include_symbols=include_symbols)
    return _architecture_view_response(view)


def _arch_layer(layer: ArchLayer) -> ArchLayerResponse:
    return ArchLayerResponse(
        id=layer.id,
        name=layer.name,
        description=layer.description,
        node_ids=layer.node_ids,
        file_count=layer.file_count,
        complexity_distribution=layer.complexity_distribution,
        health_score=layer.health_score,
        sub_groups=[
            ArchSubGroupResponse(id=sg.id, name=sg.name, node_ids=sg.node_ids)
            for sg in layer.sub_groups
        ],
        display_order=layer.display_order,
    )


def _arch_node(n: ArchNode) -> ArchNodeResponse:
    return ArchNodeResponse(
        id=n.id,
        node_type=n.node_type,
        name=n.name,
        file_path=n.file_path,
        line_range=list(n.line_range) if n.line_range else None,
        summary=n.summary,
        complexity=n.complexity,
        tags=n.tags,
        language=n.language,
        pagerank=n.pagerank,
        pagerank_percentile=n.pagerank_percentile,
        betweenness=n.betweenness,
        in_degree=n.in_degree,
        out_degree=n.out_degree,
        community_id=n.community_id,
        is_entry_point=n.is_entry_point,
        is_test=n.is_test,
        is_hotspot=n.is_hotspot,
        is_dead=n.is_dead,
        has_doc=n.has_doc,
        primary_owner=n.primary_owner,
        primary_owner_pct=n.primary_owner_pct,
        bus_factor=n.bus_factor,
    )


def _arch_edge(e: ArchEdge) -> ArchEdgeResponse:
    return ArchEdgeResponse(
        source=e.source,
        target=e.target,
        edge_type=e.edge_type,
        direction=e.direction,
        weight=e.weight,
        confidence=e.confidence,
    )


def _arch_tour_step(s: ArchTourStep) -> ArchTourStepResponse:
    return ArchTourStepResponse(
        order=s.order,
        title=s.title,
        description=s.description,
        node_ids=s.node_ids,
        target_path=s.target_path,
        layer_id=s.layer_id,
        reason=s.reason,
        depth=s.depth,
        kind=s.kind,
        page_type=s.page_type,
    )


def _architecture_view_response(view: ArchitectureView) -> ArchitectureViewResponse:
    return ArchitectureViewResponse(
        project_name=view.project_name,
        project_description=view.project_description,
        layers=[_arch_layer(la) for la in view.layers],
        nodes=[_arch_node(n) for n in view.nodes],
        edges=[_arch_edge(e) for e in view.edges],
        tour=[_arch_tour_step(s) for s in view.tour],
        total_files=view.total_files,
        total_symbols=view.total_symbols,
        total_edges=view.total_edges,
        languages=view.languages,
        frameworks=view.frameworks,
        external_systems=[_external(e) for e in view.external_systems],
        entry_points=view.entry_points,
        entry_candidates=view.entry_candidates,
    )
