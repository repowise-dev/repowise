"""/api/repos/{repo_id}/git-* — Git intelligence endpoints."""

from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, Query
from repowise.core.persistence import crud
from repowise.core.persistence.models import GitMetadata
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.mcp_server.tool_risk import _check_test_gap
from repowise.server.schemas import (
    GitMetadataResponse,
    GitSummaryResponse,
    HotspotResponse,
    OwnershipEntry,
    Paginated,
)

router = APIRouter(
    prefix="/api/repos",
    tags=["git"],
    dependencies=[Depends(verify_api_key)],
)


# ---------------------------------------------------------------------------
# Row mappers — kept out of the route bodies so they're trivial to test and
# can be reused by future endpoints (owner profile, module health, etc.).
# ---------------------------------------------------------------------------


def _hotspot_from_row(r: GitMetadata) -> HotspotResponse:
    # churn_percentile is stored on a 0–1 scale (rank / total) but every UI
    # consumer treats it as a percentile rank in 0–100. Normalize here so
    # the API contract is unambiguous and the dashboard ChurnBar / scatter
    # / hotspots-mini render correctly without per-component hacks.
    churn_pct = (r.churn_percentile or 0.0) * 100.0
    return HotspotResponse(
        file_path=r.file_path,
        commit_count_total=r.commit_count_total or 0,
        commit_count_90d=r.commit_count_90d,
        commit_count_30d=r.commit_count_30d,
        churn_percentile=churn_pct,
        temporal_hotspot_score=r.temporal_hotspot_score,
        primary_owner=r.primary_owner_name,
        primary_owner_commit_pct=r.primary_owner_commit_pct,
        recent_owner_name=r.recent_owner_name,
        recent_owner_commit_pct=r.recent_owner_commit_pct,
        is_hotspot=r.is_hotspot,
        is_stable=r.is_stable,
        bus_factor=r.bus_factor or 0,
        contributor_count=r.contributor_count or 0,
        lines_added_90d=r.lines_added_90d or 0,
        lines_deleted_90d=r.lines_deleted_90d or 0,
        avg_commit_size=r.avg_commit_size or 0.0,
        commit_categories=json.loads(r.commit_categories_json) if r.commit_categories_json else {},
        merge_commit_count_90d=r.merge_commit_count_90d or 0,
        commit_count_capped=bool(r.commit_count_capped),
        age_days=r.age_days or 0,
        last_commit_at=r.last_commit_at,
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
    response = GitMetadataResponse.from_orm(meta)
    response.test_gap = await _check_test_gap(session, repo_id, file_path)
    return response


@router.get("/{repo_id}/hotspots", response_model=Paginated[HotspotResponse])
async def get_hotspots(
    repo_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Paginated[HotspotResponse]:
    """Return the highest-churn files (hotspots), paginated.

    The previous response shape was a bare list capped at 100 rows — large
    repos with thousands of hotspots silently lost the long tail. Callers
    should treat the envelope's ``total`` as authoritative and request
    further pages via ``offset``.
    """

    base = select(GitMetadata).where(
        GitMetadata.repository_id == repo_id,
        GitMetadata.is_hotspot.is_(True),
    )
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0

    paged = base.order_by(
        GitMetadata.temporal_hotspot_score.desc().nulls_last(),
        GitMetadata.churn_percentile.desc(),
    ).limit(limit).offset(offset)
    rows = (await session.execute(paged)).scalars().all()
    items = [_hotspot_from_row(r) for r in rows]
    next_offset = offset + limit if offset + limit < total else None
    return Paginated[HotspotResponse](
        items=items,
        total=total,
        has_more=next_offset is not None,
        next_offset=next_offset,
    )


@router.get("/{repo_id}/ownership", response_model=Paginated[OwnershipEntry])
async def get_ownership(
    repo_id: str,
    granularity: str = Query("module", description="file or module"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Paginated[OwnershipEntry]:
    """Ownership breakdown for a repository, paginated.

    ``granularity=module`` groups by top-level directory; ``file`` returns
    one entry per tracked file.
    """

    result = await session.execute(select(GitMetadata).where(GitMetadata.repository_id == repo_id))
    all_meta = result.scalars().all()

    if granularity == "file":
        entries = [
            OwnershipEntry(
                module_path=m.file_path,
                primary_owner=m.primary_owner_name,
                owner_pct=m.primary_owner_commit_pct,
                file_count=1,
                is_silo=(m.primary_owner_commit_pct or 0) > 0.8,
            )
            for m in all_meta
        ]
    else:
        modules: dict[str, list] = {}
        for m in all_meta:
            parts = m.file_path.split("/")
            module = parts[0] if len(parts) > 1 else "root"
            modules.setdefault(module, []).append(m)

        entries = []
        for module_path, files in sorted(modules.items()):
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

    total = len(entries)
    page = entries[offset : offset + limit]
    next_offset = offset + limit if offset + limit < total else None
    return Paginated[OwnershipEntry](
        items=page,
        total=total,
        has_more=next_offset is not None,
        next_offset=next_offset,
    )


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
        "total": len(filtered),
    }


@router.get("/{repo_id}/git-summary", response_model=GitSummaryResponse)
async def get_git_summary(
    repo_id: str,
    top_owners_limit: int = Query(25, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> GitSummaryResponse:
    """Aggregate git health signals for a repository.

    ``top_owners_limit`` controls how many contributors are returned in
    descending order of file count. Defaults to 25 (vs. the old hardcoded
    10) so an engineering leader can see the broader contributor surface.
    """

    result = await session.execute(select(GitMetadata).where(GitMetadata.repository_id == repo_id))
    all_meta = list(result.scalars().all())

    hotspot_count = sum(1 for m in all_meta if m.is_hotspot)
    stable_count = sum(1 for m in all_meta if m.is_stable)
    # Normalize to 0–100 to match the rest of the HTTP API contract.
    avg_churn = (
        sum(m.churn_percentile for m in all_meta) / len(all_meta) * 100.0
        if all_meta
        else 0.0
    )

    owners: dict[str, int] = {}
    for m in all_meta:
        if m.primary_owner_name:
            owners[m.primary_owner_name] = owners.get(m.primary_owner_name, 0) + 1
    total = len(all_meta) or 1
    top_owners = sorted(
        [{"name": k, "file_count": v, "pct": v / total} for k, v in owners.items()],
        key=lambda x: x["file_count"],
        reverse=True,
    )[:top_owners_limit]

    return GitSummaryResponse(
        total_files=len(all_meta),
        hotspot_count=hotspot_count,
        stable_count=stable_count,
        average_churn_percentile=avg_churn,
        top_owners=top_owners,
    )
