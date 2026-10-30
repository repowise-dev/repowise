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

import gc
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

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
    ZoomMapResponse,
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
from repowise.server.services.c4_builder.structurizr import (
    DEFAULT_FILENAME as STRUCTURIZR_FILENAME,
)
from repowise.server.services.c4_builder.structurizr import to_dsl
from repowise.server.services.zoom_builder import ZoomMap, build_zoom_map

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
    co_changes: bool = Query(
        False, description="Overlay co-change relations on the dependency edges"
    ),
    session: AsyncSession = Depends(get_db_session),
) -> C4L2Response:
    view = await c4_builder.build_l2(session, repo_id, include_co_changes=co_changes)
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


@router.get(
    "/{repo_id}/c4/structurizr",
    response_class=PlainTextResponse,
    responses={200: {"content": {"text/plain": {}}}},
)
async def get_c4_structurizr(
    repo_id: str,
    standalone: bool = Query(
        False, description="Emit a complete runnable workspace instead of a model fragment"
    ),
    components: bool = Query(False, description="Include the component (L3) level"),
    externals: bool = Query(True, description="Include third-party dependencies"),
    session: AsyncSession = Depends(get_db_session),
) -> PlainTextResponse:
    """Structurizr DSL for this repository — open it in Structurizr Lite.

    By default this is the contents of a ``model`` block, meant to be included
    from the caller's own ``workspace.dsl`` so their views and styles survive
    regeneration. ``standalone=true`` returns a workspace that renders on its
    own.

    Built on demand from the persisted graph, like the other C4 endpoints —
    nothing is written at index time.

    An unknown ``repo_id`` returns 200 and an empty-but-valid model rather
    than 404, matching the l1/l2/mermaid routes beside it: they all build from
    whatever rows the id matches, and none of them looks the repository up
    first. Deliberate rather than drift — a lone 404 here would make this the
    one C4 route with its own contract. The CLI does guard the case, because
    there a wrong path is a typo worth naming.
    """
    model = await c4_builder.build_model(session, repo_id, include_components=components)
    dsl = to_dsl(
        model,
        standalone=standalone,
        include_components=components,
        include_externals=externals,
    )
    filename = "workspace.dsl" if standalone else STRUCTURIZR_FILENAME
    return PlainTextResponse(
        dsl,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Dataclass → Pydantic adapters (kept tiny on purpose)
# ---------------------------------------------------------------------------


def _system(s: System) -> C4SystemResponse:
    return C4SystemResponse(id=s.id, name=s.name, description=s.description)


def _person(p: Person) -> C4PersonResponse:
    return C4PersonResponse(id=p.id, name=p.name, description=p.description, kind=p.kind)


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
        coupling=r.coupling,
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


@router.get("/{repo_id}/zoom-map", response_model=ZoomMapResponse)
async def get_zoom_map(
    repo_id: str,
    max_depth: int | None = Query(
        None, ge=1, description="Cap levels served below the (focus) root; omit for full"
    ),
    focus: str | None = Query(
        None, description="Node id to scope the served subtree to (lazy drill-down)"
    ),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """The continuous-zoom containment tree (system -> layer -> ... -> file).

    Derived on demand from the persisted graph, like the C4 views. ``focus`` +
    ``max_depth`` let the canvas fetch deeper subtrees lazily for large repos.

    The body is the built model's own JSON: ``response_model`` documents the
    shape, and returning a ``Response`` keeps FastAPI from validating and
    encoding ~25k models a second time on versions without its direct-dump path.
    """
    with _cyclic_gc_paused():
        zoom = await build_zoom_map(session, repo_id, max_depth=max_depth, focus=focus)
        body = _zoom_response(zoom).model_dump_json()
    return Response(content=body, media_type="application/json")


_gc_pause_lock = threading.Lock()
_gc_pause_held = False


@contextmanager
def _cyclic_gc_paused() -> Iterator[None]:
    """Hold off cyclic garbage collection while a zoom map is built and encoded.

    The build allocates enough container objects to trigger several full
    collections, each of which walks the whole long-lived server heap; on a
    7k-file repository that walk was a third of the request. Reference counting
    still frees everything acyclic; cycles made meanwhile wait for the next
    collection. The request that disabled the collector is the one that
    restores it: an overlapping request runs inside that pause but never
    extends it, so a stream of requests cannot keep the collector off for
    longer than one build. A collector that was already off stays off.
    """
    global _gc_pause_held
    with _gc_pause_lock:
        owner = not _gc_pause_held and gc.isenabled()
        if owner:
            gc.disable()
            _gc_pause_held = True
    try:
        yield
    finally:
        if owner:
            with _gc_pause_lock:
                _gc_pause_held = False
                gc.enable()


def _zoom_response(zoom: ZoomMap) -> ZoomMapResponse:
    """One validation pass over plain data instead of a model per node.

    The relations, externals, containers and metrics are read straight off the
    builder's dataclasses (their fields match the response models one to one);
    only a node needs a dict, for the rounding. Building ~25k models one by one
    from Python cost more than the rest of the response put together.
    """
    return ZoomMapResponse.model_validate(
        {
            "root_id": zoom.root_id,
            "project_name": zoom.project_name,
            "total_files": zoom.total_files,
            "unclaimed_files": zoom.unclaimed_files,
            "max_depth": zoom.max_depth,
            "truncated": zoom.truncated,
            "nodes": [
                {
                    "id": n.id,
                    "parent_id": n.parent_id,
                    "level": n.level,
                    "kind": n.kind,
                    "name": n.name,
                    "path": n.path,
                    "children": n.children,
                    "importance": round(n.importance, 4),
                    "sibling_rank": n.sibling_rank,
                    "metrics": n.metrics,
                    "summary": n.summary,
                    "language": n.language,
                    "page_id": n.page_id,
                    "health_score": (
                        round(n.health_score, 2) if n.health_score is not None else None
                    ),
                    "is_entry_point": n.is_entry_point,
                    "is_hotspot": n.is_hotspot,
                    "is_dead": n.is_dead,
                    "is_test": n.is_test,
                    "on_flow": n.on_flow,
                    "technologies": n.technologies,
                    "container_ids": n.container_ids,
                }
                # Stable order so the response is deterministic regardless of the
                # builder's internal insertion order.
                for n in sorted(zoom.nodes.values(), key=lambda node: node.id)
            ],
            "relations": zoom.relations,
            "externals": zoom.externals,
            "external_relations": zoom.external_relations,
            "containers": zoom.containers,
        },
        from_attributes=True,
    )
