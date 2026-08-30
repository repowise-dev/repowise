"""/api/repos/{repo_id}/external-systems — the dependency registry.

The ``external_systems`` table (populated by the manifest parsers during
ingestion) previously only fed the C4 L1 boundary. This endpoint exposes the
full registry — category, ecosystem, version, dev/prod split and the manifest
each dependency was declared in — for the Architecture Dependencies view.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.external_systems import build_registry
from repowise.core.persistence.models import ExternalSystem
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas.external_systems import (
    ExternalSystemImportingFilesResponse,
    ExternalSystemRelationshipGraphResponse,
    ExternalSystemsResponse,
    ExternalSystemsSummaryResponse,
)
from repowise.server.services.external_system_relationships import (
    DEFAULT_FILE_LIMIT,
    DEFAULT_RELATIONSHIP_EDGE_LIMIT,
    DEFAULT_RELATIONSHIP_NODE_LIMIT,
    MAX_FILE_LIMIT,
    MAX_RELATIONSHIP_EDGE_LIMIT,
    MAX_RELATIONSHIP_NODE_LIMIT,
    build_external_system_importing_files,
    build_external_system_relationship_graph,
)
from repowise.server.services.external_systems import (
    MAX_SUMMARY_LIMIT,
    build_external_systems_summary,
)

router = APIRouter(
    prefix="/api/repos",
    tags=["external-systems"],
    dependencies=[Depends(verify_api_key)],
)


@router.get(
    "/{repo_id}/external-systems/summary",
    response_model=ExternalSystemsSummaryResponse,
)
async def summarize_external_systems(
    repo_id: str,
    scope: Literal["primary", "all"] = "primary",
    limit: int = Query(default=200, ge=1, le=MAX_SUMMARY_LIMIT),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> ExternalSystemsSummaryResponse:
    """Canonical packages joined to already-persisted import evidence."""
    return await build_external_systems_summary(
        session, repo_id, scope=scope, limit=limit, offset=offset
    )


@router.get(
    "/{repo_id}/external-systems/{package_key:path}/graph/files",
    response_model=ExternalSystemImportingFilesResponse,
)
async def external_system_importing_files(
    repo_id: str,
    package_key: str,
    aggregate_key: str = Query(...),
    scope: Literal["primary", "all"] = "primary",
    limit: int = Query(default=DEFAULT_FILE_LIMIT, ge=1, le=MAX_FILE_LIMIT),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> ExternalSystemImportingFilesResponse:
    """Paginate importing files only after a relationship aggregate is opened."""
    try:
        response = await build_external_system_importing_files(
            session,
            repo_id,
            package_key,
            aggregate_key,
            scope=scope,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if response is None:
        raise HTTPException(status_code=404, detail="Declared package not found in this scope")
    return response


@router.get(
    "/{repo_id}/external-systems/{package_key:path}/graph",
    response_model=ExternalSystemRelationshipGraphResponse,
)
async def external_system_relationship_graph(
    repo_id: str,
    package_key: str,
    scope: Literal["primary", "all"] = "primary",
    node_limit: int = Query(
        default=DEFAULT_RELATIONSHIP_NODE_LIMIT, ge=1, le=MAX_RELATIONSHIP_NODE_LIMIT
    ),
    edge_limit: int = Query(
        default=DEFAULT_RELATIONSHIP_EDGE_LIMIT, ge=1, le=MAX_RELATIONSHIP_EDGE_LIMIT
    ),
    session: AsyncSession = Depends(get_db_session),
) -> ExternalSystemRelationshipGraphResponse:
    """Show bounded first-party communities related to one declared package."""
    try:
        response = await build_external_system_relationship_graph(
            session,
            repo_id,
            package_key,
            scope=scope,
            node_limit=node_limit,
            edge_limit=edge_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if response is None:
        raise HTTPException(status_code=404, detail="Declared package not found in this scope")
    return response


@router.get("/{repo_id}/external-systems", response_model=ExternalSystemsResponse)
async def list_external_systems(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ExternalSystemsResponse:
    """Every declared third-party dependency, unfiltered and undeduplicated.

    Unlike the C4 L1 view (which dedupes by name for the diagram boundary),
    this returns one row per (name, declared_in) so a monorepo's per-package
    manifests stay distinguishable. Sorted by category prominence, then name.
    """
    rows = (
        (
            await session.execute(
                select(ExternalSystem).where(ExternalSystem.repository_id == repo_id)
            )
        )
        .scalars()
        .all()
    )
    return ExternalSystemsResponse.model_validate(build_registry(rows).as_dict())
