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
    ArchitectureViewResponse,
    C4ComponentResponse,
    C4ContainerResponse,
    C4L1Response,
    C4L2Response,
    C4L3Response,
    C4PersonResponse,
    C4RelationResponse,
    C4SystemResponse,
)
from repowise.server.services import c4_builder
from repowise.server.services.c4_builder.mermaid import (
    to_mermaid_l1,
    to_mermaid_l2,
    to_mermaid_l3,
)
from repowise.server.services.c4_builder.models import (
    Component,
    Container,
    Person,
    Relation,
    System,
)
from repowise.server.services.c4_builder.serialize import (
    build_architecture_view_response,
    external_system_response,
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
        external_systems=[external_system_response(e) for e in view.external_systems],
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
        external_systems=[external_system_response(e) for e in view.external_systems],
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
        external_systems=[external_system_response(e) for e in view.external_systems],
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
# Architecture view endpoint — thin wrapper over services.c4_builder.serialize
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/architecture-view")
async def get_architecture_view(
    repo_id: str,
    include_symbols: bool = Query(False, description="Include symbol-level nodes"),
    session: AsyncSession = Depends(get_db_session),
) -> ArchitectureViewResponse:
    return await build_architecture_view_response(
        session, repo_id, include_symbols=include_symbols
    )
