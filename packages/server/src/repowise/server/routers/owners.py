"""/api/repos/{repo_id}/owners — Contributor profile endpoints.

The owners directory and per-owner profile is the engineering-leader view:
"who is doing what, where is the risk, who reviews this code." It composes
existing GitMetadata + DeadCodeFinding rows; no ingestion changes.
"""

from __future__ import annotations

from urllib.parse import unquote

from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, Query
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import (
    OwnerCoAuthor,
    OwnerFileEntry,
    OwnerListEntry,
    OwnerModuleRollup,
    OwnerProfileResponse,
    Paginated,
)
from repowise.server.schemas.ownership import OwnerAgentCollab
from repowise.server.services.owner_profile import (
    _OwnerAccumulator,
    aggregate_owners,
    module_share,
    silo_modules,
)

router = APIRouter(
    prefix="/api/repos",
    tags=["owners"],
    dependencies=[Depends(verify_api_key)],
)


# ---------------------------------------------------------------------------
# Shapers
# ---------------------------------------------------------------------------


def _to_list_entry(acc: _OwnerAccumulator, module_totals: dict[str, int]) -> OwnerListEntry:
    return OwnerListEntry(
        key=acc.key,
        name=acc.name,
        email=acc.email,
        files_owned=acc.files_owned,
        hotspots_owned=acc.hotspots_owned,
        silo_modules=silo_modules(acc, module_totals),
        dead_code_files_owned=len(acc.dead_code_files),
        dead_code_lines_owned=acc.dead_code_lines,
        commit_count_90d=acc.commit_count_90d,
        last_commit_at=acc.last_commit_at,
        bus_factor_risk_files=acc.bus_factor_risk_files,
    )


def _to_profile(acc: _OwnerAccumulator, module_totals: dict[str, int]) -> OwnerProfileResponse:
    shares = module_share(acc, module_totals)

    modules = [
        OwnerModuleRollup(
            module_path=mod,
            file_count=acc.module_files.get(mod, 0),
            hotspot_count=acc.module_hotspots.get(mod, 0),
            dominant_pct=shares.get(mod, 0.0),
        )
        for mod in sorted(
            acc.module_files,
            key=lambda m: (acc.module_files[m], acc.module_hotspots.get(m, 0)),
            reverse=True,
        )
    ]

    # Top files by attributed commit count, then churn.
    top_files: list[OwnerFileEntry] = []
    sorted_paths = sorted(
        acc.files_touched,
        key=lambda fp: (
            acc.files_touched[fp],
            (acc.file_meta[fp].churn_percentile or 0.0),
        ),
        reverse=True,
    )[:50]
    for fp in sorted_paths:
        m = acc.file_meta[fp]
        top_files.append(
            OwnerFileEntry(
                file_path=fp,
                commit_count_90d=acc.files_touched[fp],
                churn_percentile=(m.churn_percentile or 0.0) * 100.0,
                bus_factor=m.bus_factor or 0,
                is_hotspot=bool(m.is_hotspot),
                last_commit_at=m.last_commit_at,
                primary_owner_commit_pct=m.primary_owner_commit_pct,
            )
        )

    co_authors: list[OwnerCoAuthor] = []
    total_touched = max(len(acc.files_touched), 1)
    for other_key, shared in acc.coauthor_shared.most_common(25):
        name, email = acc.coauthor_meta.get(other_key, (other_key, None))
        co_authors.append(
            OwnerCoAuthor(
                name=name,
                email=email,
                shared_files=shared,
                co_change_strength=shared / total_touched,
            )
        )

    agent_collab: OwnerAgentCollab | None = None
    if acc.owned_attributed_commits > 0 or acc.owned_agent_commits > 0:
        agent_collab = OwnerAgentCollab(
            files_with_agent_commits=acc.owned_files_with_agents,
            agent_commit_count=acc.owned_agent_commits,
            agent_share_pct=(
                acc.owned_agent_commits / acc.owned_attributed_commits * 100.0
                if acc.owned_attributed_commits
                else None
            ),
            tier_counts=dict(acc.owned_agent_tier_counts),
        )

    return OwnerProfileResponse(
        key=acc.key,
        name=acc.name,
        email=acc.email,
        files_owned=acc.files_owned,
        hotspots_owned=acc.hotspots_owned,
        silo_modules=silo_modules(acc, module_totals),
        dead_code_files_owned=len(acc.dead_code_files),
        dead_code_lines_owned=acc.dead_code_lines,
        commit_count_90d=acc.commit_count_90d,
        last_commit_at=acc.last_commit_at,
        first_commit_at=acc.first_commit_at,
        bus_factor_risk_files=acc.bus_factor_risk_files,
        lines_added_90d_est=int(acc.lines_added_90d_est),
        lines_deleted_90d_est=int(acc.lines_deleted_90d_est),
        modules=modules,
        top_files=top_files,
        files_touched_total=len(acc.files_touched),
        co_authors=co_authors,
        co_authors_total=len(acc.coauthor_shared),
        commit_categories=dict(acc.commit_categories),
        agent_collab=agent_collab,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/owners", response_model=Paginated[OwnerListEntry])
async def list_owners(
    repo_id: str,
    q: str = Query("", description="Substring filter on name or email"),
    sort: str = Query(
        "files_owned",
        description="One of: files_owned, hotspots_owned, commit_count_90d, dead_code_lines_owned, bus_factor_risk_files",
    ),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Paginated[OwnerListEntry]:
    """Directory of contributors with rollup stats — engineering-leader view."""

    accs, module_totals = await aggregate_owners(session, repo_id)
    entries = [_to_list_entry(a, module_totals) for a in accs.values() if a.key]

    if q:
        needle = q.lower()
        entries = [
            e
            for e in entries
            if needle in e.name.lower() or (e.email and needle in e.email.lower())
        ]

    sort_key = {
        "files_owned": lambda e: e.files_owned,
        "hotspots_owned": lambda e: e.hotspots_owned,
        "commit_count_90d": lambda e: e.commit_count_90d,
        "dead_code_lines_owned": lambda e: e.dead_code_lines_owned,
        "bus_factor_risk_files": lambda e: e.bus_factor_risk_files,
    }.get(sort, lambda e: e.files_owned)
    entries.sort(key=sort_key, reverse=True)

    total = len(entries)
    page = entries[offset : offset + limit]
    next_offset = offset + limit if offset + limit < total else None
    return Paginated[OwnerListEntry](
        items=page,
        total=total,
        has_more=next_offset is not None,
        next_offset=next_offset,
    )


@router.get(
    "/{repo_id}/owners/{owner_key:path}",
    response_model=OwnerProfileResponse,
)
async def get_owner(
    repo_id: str,
    owner_key: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> OwnerProfileResponse:
    """Full profile for one contributor.

    ``owner_key`` is either the contributor's email (preferred) or
    ``name:<display name>`` for entries with no email recorded. URL-encoded
    by the caller.
    """

    key = unquote(owner_key).strip().lower() if "@" in owner_key else unquote(owner_key)
    accs, module_totals = await aggregate_owners(session, repo_id)
    acc = accs.get(key)
    if acc is None:
        # Last-ditch fallback: case-insensitive lookup by name.
        for cand in accs.values():
            if cand.name.lower() == key.lower():
                acc = cand
                break
    if acc is None:
        raise HTTPException(status_code=404, detail="Owner not found")
    return _to_profile(acc, module_totals)
