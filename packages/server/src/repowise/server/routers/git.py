"""/api/repos/{repo_id}/git-* — Git intelligence endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.core.persistence.models import GitMetadata
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import (
    GitMetadataResponse,
    GitSummaryResponse,
    HotspotResponse,
    OwnershipEntry,
)

router = APIRouter(
    prefix="/api/repos",
    tags=["git"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("/{repo_id}/git-metadata", response_model=GitMetadataResponse)
async def get_git_metadata(
    repo_id: str,
    file_path: str = Query(..., description="Relative file path"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> GitMetadataResponse:
    """Get git metadata for a specific file."""
    meta = await crud.get_git_metadata(session, repo_id, file_path)
    if meta is None:
        raise HTTPException(status_code=404, detail="Git metadata not found")
    return GitMetadataResponse.from_orm(meta)


@router.get("/{repo_id}/hotspots", response_model=list[HotspotResponse])
async def get_hotspots(
    repo_id: str,
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[HotspotResponse]:
    """Get the highest-churn files (hotspots) for a repository."""
    result = await session.execute(
        select(GitMetadata)
        .where(
            GitMetadata.repository_id == repo_id,
            GitMetadata.is_hotspot.is_(True),
        )
        .order_by(GitMetadata.churn_percentile.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return [
        HotspotResponse(
            file_path=r.file_path,
            commit_count_90d=r.commit_count_90d,
            commit_count_30d=r.commit_count_30d,
            churn_percentile=r.churn_percentile,
            primary_owner=r.primary_owner_name,
            is_hotspot=r.is_hotspot,
            is_stable=r.is_stable,
            bus_factor=r.bus_factor or 0,
            contributor_count=r.contributor_count or 0,
            lines_added_90d=r.lines_added_90d or 0,
            lines_deleted_90d=r.lines_deleted_90d or 0,
            avg_commit_size=r.avg_commit_size or 0.0,
            commit_categories=json.loads(r.commit_categories_json) if r.commit_categories_json else {},
        )
        for r in rows
    ]


@router.get("/{repo_id}/ownership", response_model=list[OwnershipEntry])
async def get_ownership(
    repo_id: str,
    granularity: str = Query("module", description="file or module"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[OwnershipEntry]:
    """Get ownership breakdown for a repository."""
    result = await session.execute(select(GitMetadata).where(GitMetadata.repository_id == repo_id))
    all_meta = result.scalars().all()

    if granularity == "file":
        return [
            OwnershipEntry(
                module_path=m.file_path,
                primary_owner=m.primary_owner_name,
                owner_pct=m.primary_owner_commit_pct,
                file_count=1,
                is_silo=(m.primary_owner_commit_pct or 0) > 0.8,
            )
            for m in all_meta
        ]

    # Group by top-level directory (module)
    modules: dict[str, list] = {}
    for m in all_meta:
        parts = m.file_path.split("/")
        module = parts[0] if len(parts) > 1 else "root"
        modules.setdefault(module, []).append(m)

    entries = []
    for module_path, files in sorted(modules.items()):
        # Find the most common owner in this module
        owners: dict[str, int] = {}
        for f in files:
            if f.primary_owner_name:
                owners[f.primary_owner_name] = owners.get(f.primary_owner_name, 0) + 1
        if owners:
            top_owner = max(owners, key=owners.get)  # type: ignore[arg-type]
            owner_pct = owners[top_owner] / len(files)
        else:
            top_owner = None
            owner_pct = 0.0

        entries.append(
            OwnershipEntry(
                module_path=module_path,
                primary_owner=top_owner,
                owner_pct=owner_pct,
                file_count=len(files),
                is_silo=owner_pct > 0.8,
            )
        )
    return entries


@router.get("/{repo_id}/co-changes")
async def get_co_changes(
    repo_id: str,
    file_path: str = Query(..., description="Relative file path"),
    min_count: int = Query(3, ge=1),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Get files that frequently change together with the given file."""
    meta = await crud.get_git_metadata(session, repo_id, file_path)
    if meta is None:
        raise HTTPException(status_code=404, detail="Git metadata not found")

    partners = json.loads(meta.co_change_partners_json)
    filtered = [p for p in partners if p.get("co_change_count", 0) >= min_count]

    return {
        "file_path": file_path,
        "co_change_partners": filtered,
    }


@router.get("/{repo_id}/git-summary", response_model=GitSummaryResponse)
async def get_git_summary(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> GitSummaryResponse:
    """Get aggregate git health signals for a repository."""
    result = await session.execute(select(GitMetadata).where(GitMetadata.repository_id == repo_id))
    all_meta = list(result.scalars().all())

    hotspot_count = sum(1 for m in all_meta if m.is_hotspot)
    stable_count = sum(1 for m in all_meta if m.is_stable)
    avg_churn = sum(m.churn_percentile for m in all_meta) / len(all_meta) if all_meta else 0.0

    # Top owners by file count
    owners: dict[str, int] = {}
    for m in all_meta:
        if m.primary_owner_name:
            owners[m.primary_owner_name] = owners.get(m.primary_owner_name, 0) + 1
    total = len(all_meta) or 1
    top_owners = sorted(
        [
            {"name": k, "file_count": v, "pct": v / total}
            for k, v in owners.items()
        ],
        key=lambda x: x["file_count"],
        reverse=True,
    )[:10]

    return GitSummaryResponse(
        total_files=len(all_meta),
        hotspot_count=hotspot_count,
        stable_count=stable_count,
        average_churn_percentile=avg_churn,
        top_owners=top_owners,
    )
