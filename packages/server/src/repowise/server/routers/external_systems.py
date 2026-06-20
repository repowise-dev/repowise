"""/api/repos/{repo_id}/external-systems — the dependency registry.

The ``external_systems`` table (populated by the manifest parsers during
ingestion) previously only fed the C4 L1 boundary. This endpoint exposes the
full registry — category, ecosystem, version, dev/prod split and the manifest
each dependency was declared in — for the Architecture Dependencies view.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import ExternalSystem
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas.external_systems import (
    ExternalSystemEntry,
    ExternalSystemsResponse,
)

router = APIRouter(
    prefix="/api/repos",
    tags=["external-systems"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("/{repo_id}/external-systems", response_model=ExternalSystemsResponse)
async def list_external_systems(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
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
    category_order = {"framework": 0, "service": 1, "tool": 2, "library": 3}
    items = sorted(
        (
            ExternalSystemEntry(
                name=r.name,
                display_name=r.display_name or r.name,
                ecosystem=r.ecosystem,
                category=r.category,
                io_kind=r.io_kind,
                version=r.version,
                declared_in=r.declared_in,
                is_dev_dep=bool(r.is_dev_dep),
            )
            for r in rows
        ),
        key=lambda e: (category_order.get(e.category, 9), e.name.lower(), e.declared_in),
    )
    ecosystems = sorted({e.ecosystem for e in items})
    manifests = sorted({e.declared_in for e in items})
    return ExternalSystemsResponse(
        items=items,
        total=len(items),
        prod_count=sum(1 for e in items if not e.is_dev_dep),
        dev_count=sum(1 for e in items if e.is_dev_dep),
        ecosystems=ecosystems,
        manifests=manifests,
    )
