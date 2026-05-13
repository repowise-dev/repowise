"""/api/repos/{repo_id}/modules — Module Health endpoints.

Per-module rollups of ownership, churn, dead-code, docs, and decisions.
No core ingestion changes.
"""

from __future__ import annotations

from urllib.parse import unquote

from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, Query
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import (
    ModuleHealthDetail,
    ModuleHealthOwner,
    ModuleHealthSummary,
    Paginated,
)
from repowise.server.services.module_health import (
    aggregate_modules,
    detail_extras,
    summarize,
)

router = APIRouter(
    prefix="/api/repos",
    tags=["modules"],
    dependencies=[Depends(verify_api_key)],
)


@router.get(
    "/{repo_id}/modules/health",
    response_model=Paginated[ModuleHealthSummary],
)
async def list_module_health(
    repo_id: str,
    sort: str = Query("health_score", description="health_score | hotspot_count | dead_code_lines | file_count"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Paginated[ModuleHealthSummary]:
    accs = await aggregate_modules(session, repo_id)
    rows = [ModuleHealthSummary(**summarize(a)) for a in accs.values()]

    # Default ascending for health_score (worst first); descending for others.
    if sort == "health_score":
        rows.sort(key=lambda r: r.health_score)
    elif sort == "hotspot_count":
        rows.sort(key=lambda r: r.hotspot_count, reverse=True)
    elif sort == "dead_code_lines":
        rows.sort(key=lambda r: r.dead_code_lines, reverse=True)
    elif sort == "file_count":
        rows.sort(key=lambda r: r.file_count, reverse=True)

    total = len(rows)
    page = rows[offset : offset + limit]
    next_offset = offset + limit if offset + limit < total else None
    return Paginated[ModuleHealthSummary](
        items=page,
        total=total,
        has_more=next_offset is not None,
        next_offset=next_offset,
    )


@router.get(
    "/{repo_id}/modules/health/{module_path:path}",
    response_model=ModuleHealthDetail,
)
async def get_module_health(
    repo_id: str,
    module_path: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ModuleHealthDetail:
    accs = await aggregate_modules(session, repo_id)
    acc = accs.get(unquote(module_path))
    if acc is None:
        raise HTTPException(status_code=404, detail="Module not found")

    base = summarize(acc)
    extras = detail_extras(acc)
    return ModuleHealthDetail(
        **base,
        owners=[ModuleHealthOwner(**o) for o in extras["owners"]],
        top_hotspots=extras["top_hotspots"],
        governing_decisions=extras["governing_decisions"],
        contributor_count=extras["contributor_count"],
    )
