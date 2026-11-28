"""/api/repos/{repo_id}/areas/* — directory rollups at any depth.

Thin: the fold is :mod:`repowise.core.analysis.areas`, the read model and its
cache are :mod:`repowise.server.services.areas`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.areas import ROOT_AREA, normalize_area
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.routers.held_origins import HOLD_QUERY, INCLUDE_QUERY
from repowise.server.schemas.areas import AreaDetailResponse, AreaTreeResponse
from repowise.server.services.areas import (
    MAX_TREE_DEPTH,
    area_detail_payload,
    area_tree_payload,
    held_origins,
    load_area_model,
)

router = APIRouter(
    prefix="/api/repos",
    tags=["areas"],
    dependencies=[Depends(verify_api_key)],
)


async def _require_repo(session: AsyncSession, repo_id: str) -> None:
    if await crud.get_repository(session, repo_id) is None:
        raise HTTPException(status_code=404, detail="Repository not found")


def _no_area(area: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"No scored files under area '{area}'")


# Registered before the detail route, which would otherwise read "tree" as a path.
# Ceiling: a top-level directory named ``tree`` is reachable only through
# ``/areas/tree?root=tree``; a detail alias would lift it if a repo needs one.
@router.get("/{repo_id}/areas/tree", response_model=AreaTreeResponse)
async def get_area_tree(
    repo_id: str,
    root: str = Query(
        ROOT_AREA, max_length=1024, description="Area to unfold; the repo root by default."
    ),
    depth: int = Query(1, ge=1, le=MAX_TREE_DEPTH, description="Levels below the root to include."),
    hold: str = HOLD_QUERY,
    include: str = INCLUDE_QUERY,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """The root area's rollup plus every directory up to *depth* levels below it.

    Complete over every scored file, not the map's drawn field.
    """
    await _require_repo(session, repo_id)
    area = normalize_area(root)
    model = await load_area_model(session, repo_id, held_origins(hold, include))
    payload = area_tree_payload(model, area, depth)
    if payload is None:
        raise _no_area(area)
    return payload


@router.get("/{repo_id}/areas", response_model=AreaDetailResponse)
@router.get("/{repo_id}/areas/{area_path:path}", response_model=AreaDetailResponse)
async def get_area_detail(
    repo_id: str,
    area_path: str = ROOT_AREA,
    hold: str = HOLD_QUERY,
    include: str = INCLUDE_QUERY,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """One area's card: rollup, worst files, hotspots, owners, trend, findings, test reach."""
    await _require_repo(session, repo_id)
    area = normalize_area(area_path)
    model = await load_area_model(session, repo_id, held_origins(hold, include))
    payload = await area_detail_payload(session, repo_id, model, area)
    if payload is None:
        raise _no_area(area)
    return payload
