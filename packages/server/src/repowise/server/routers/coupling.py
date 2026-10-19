"""/api/repos/{repo_id}/coupling — repo-wide change coupling, by file pair or by area.

Surfaces the per-file co-change partners the git indexer already persists
(``GitMetadata.co_change_partners_json``) as a deduplicated, undirected edge
list, and folds it into area pairs. Pure surfacing: no recompute, no LLM.
Thin: the fold is :mod:`repowise.core.analysis.coupling`, the read model and
its cache are :mod:`repowise.server.services.coupling`.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.areas import ROOT_AREA, normalize_area
from repowise.core.analysis.coupling import (
    AREA_PAIR_LIMIT,
    MAX_DEPTH,
    TOP_PAIRS,
    CouplingScope,
)
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import (
    CouplingEdgeResponse,
    CouplingGraphResponse,
    CouplingNodeResponse,
)
from repowise.server.schemas.coupling import CouplingAreasResponse
from repowise.server.services.coupling import (
    area_coupling_payload,
    coupling_answer,
    graph_payload,
)

router = APIRouter(
    tags=["coupling"],
    dependencies=[Depends(verify_api_key)],
)


def _optional_area(raw: str | None) -> str | None:
    return None if raw is None else normalize_area(raw)


def coupling_scope(
    depth: int | None = Query(
        None, ge=1, le=MAX_DEPTH, description="Area depth below the root; chosen when omitted."
    ),
    root: str = Query(ROOT_AREA, max_length=1024, description="Area the view is rooted at."),
    min_support: int = Query(0, ge=0, le=10_000, description="Shared commits a pair needs."),
    unexplained: bool = Query(False, description="Only pairs no dependency explains."),
    cross_area: bool = Query(False, description="Only pairs spanning two areas."),
    exclude_tests: bool = Query(False, description="Leave out pairs touching test material."),
    recent_days: int | None = Query(
        None, ge=1, le=36_500, description="Only pairs co-changed this recently, by index time."
    ),
) -> CouplingScope:
    """The filters both coupling routes share; ``depth=0`` stands for "choose"."""
    return CouplingScope(
        depth=depth or 0,
        root=normalize_area(root),
        min_support=min_support,
        unexplained=unexplained,
        cross_area=cross_area,
        exclude_tests=exclude_tests,
        recent_days=recent_days,
    )


async def _require_repo(session: AsyncSession, repo_id: str) -> None:
    if await crud.get_repository(session, repo_id) is None:
        raise HTTPException(status_code=404, detail="Repository not found")


@router.get("/api/repos/{repo_id}/coupling", response_model=CouplingGraphResponse)
async def coupling(
    repo_id: str,
    limit: int = Query(200, ge=1, le=1000),
    area: str | None = Query(
        None, max_length=1024, description="Only pairs with a file placed in this area."
    ),
    partner: str | None = Query(
        None, max_length=1024, description="With ``area``: only pairs whose other file is placed here."
    ),
    scope: CouplingScope = Depends(coupling_scope),
    session: AsyncSession = Depends(get_db_session),
) -> CouplingGraphResponse:
    """The repo's change-coupling graph under the filters (strongest *limit* edges).

    Co-change is a temporal hint (files committed together), not a verified
    dependency; ``strength`` is the decay-weighted count, not a percentage,
    while ``support`` is the plain number of shared commits. ``total_edges``
    and ``coupled_files`` count what the filters kept, before the cap.
    """
    await _require_repo(session, repo_id)
    if partner is not None and area is None:
        raise HTTPException(status_code=422, detail="partner needs area")
    # An empty ``area`` is the root-files area, as its area-pair row names it.
    scope = replace(scope, area=_optional_area(area), partner=_optional_area(partner))
    graph, depth = await coupling_answer(
        session, repo_id, ("graph", scope, limit), lambda m: graph_payload(m, scope, limit)
    )
    return CouplingGraphResponse(
        nodes=[CouplingNodeResponse(**vars(n)) for n in graph.nodes],
        edges=[CouplingEdgeResponse(**vars(e)) for e in graph.edges],
        total_edges=graph.total_edges,
        coupled_files=graph.coupled_files,
        total_files=graph.total_files,
        depth=depth,
    )


@router.get("/api/repos/{repo_id}/coupling/areas", response_model=CouplingAreasResponse)
async def coupling_areas(
    repo_id: str,
    limit: int = Query(AREA_PAIR_LIMIT, ge=1, le=200, description="Area pairs to return."),
    top_pairs: int = Query(TOP_PAIRS, ge=1, le=20, description="File pairs per area pair."),
    scope: CouplingScope = Depends(coupling_scope),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Which areas change together, and how many of their file pairs no
    dependency explains. Ranked by unexplained pairs, then strength."""
    await _require_repo(session, repo_id)
    return await coupling_answer(
        session,
        repo_id,
        ("areas", scope, limit, top_pairs),
        lambda m: area_coupling_payload(m, scope, limit, top_pairs),
    )
