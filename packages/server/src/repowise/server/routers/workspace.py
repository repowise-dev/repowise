"""/api/workspace — Workspace intelligence endpoints.

Exposes workspace metadata, cross-repo co-changes, and API contract data
through REST. All data is read from ``app.state`` (populated at server
startup from ``.repowise-workspace/`` JSON files) — no DB access needed.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from repowise.server.deps import (
    get_cross_repo_enricher,
    get_workspace_config,
    verify_api_key,
)
from repowise.server.schemas import (
    WorkspaceCoChangeEntry,
    WorkspaceCoChangesResponse,
    WorkspaceContractEntry,
    WorkspaceContractLinkEntry,
    WorkspaceContractsResponse,
    WorkspaceContractSummary,
    WorkspaceCrossRepoSummary,
    WorkspaceRepoEntry,
    WorkspaceResponse,
)

router = APIRouter(
    prefix="/api/workspace",
    tags=["workspace"],
    dependencies=[Depends(verify_api_key)],
)


def _require_workspace(ws_config: object) -> None:
    """Raise 404 if not in workspace mode."""
    if ws_config is None:
        raise HTTPException(status_code=404, detail="Not running in workspace mode")


# ---------------------------------------------------------------------------
# GET /api/workspace
# ---------------------------------------------------------------------------


@router.get("", response_model=WorkspaceResponse)
async def get_workspace(
    request: Request,
    ws_config=Depends(get_workspace_config),
    enricher=Depends(get_cross_repo_enricher),
):
    """Workspace metadata and summary statistics.

    Returns ``is_workspace=false`` with empty data in single-repo mode —
    the web UI uses this for mode detection, so this endpoint never 404s.
    """
    if ws_config is None:
        return WorkspaceResponse(is_workspace=False)

    repo_entries = [
        WorkspaceRepoEntry(
            alias=r.alias,
            path=r.path,
            is_primary=r.is_primary,
            indexed_at=r.indexed_at,
            last_commit_at_index=r.last_commit_at_index,
        )
        for r in ws_config.repos
    ]

    cross_repo_summary = None
    contract_summary = None

    if enricher is not None:
        summary = enricher.get_cross_repo_summary()
        cross_repo_summary = WorkspaceCrossRepoSummary(**summary)
        if enricher.has_contract_data:
            cs = enricher.get_contract_summary()
            contract_summary = WorkspaceContractSummary(**cs)

    ws_root = getattr(request.app.state, "workspace_root", None)

    return WorkspaceResponse(
        is_workspace=True,
        workspace_root=ws_root,
        workspace_name=Path(ws_root).name if ws_root else None,
        repos=repo_entries,
        default_repo=ws_config.default_repo,
        cross_repo_summary=cross_repo_summary,
        contract_summary=contract_summary,
    )


# ---------------------------------------------------------------------------
# GET /api/workspace/contracts
# ---------------------------------------------------------------------------


@router.get("/contracts", response_model=WorkspaceContractsResponse)
async def get_contracts(
    ws_config=Depends(get_workspace_config),
    enricher=Depends(get_cross_repo_enricher),
    contract_type: str | None = Query(None, description="Filter: http, grpc, or topic"),
    repo: str | None = Query(None, description="Filter by repo alias"),
    role: str | None = Query(None, description="Filter: provider or consumer"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """All detected contracts and matched links with optional filtering."""
    _require_workspace(ws_config)

    if enricher is None:
        return WorkspaceContractsResponse(
            contracts=[], links=[], total_contracts=0, total_links=0,
        )

    contracts = list(getattr(enricher, "_contracts", []))
    links = list(getattr(enricher, "_contract_links", []))

    # Apply filters to contracts
    if contract_type:
        contracts = [c for c in contracts if c.get("contract_type") == contract_type]
        links = [lk for lk in links if lk.get("contract_type") == contract_type]
    if repo:
        contracts = [c for c in contracts if c.get("repo") == repo]
        links = [
            lk for lk in links
            if lk.get("provider_repo") == repo or lk.get("consumer_repo") == repo
        ]
    if role:
        contracts = [c for c in contracts if c.get("role") == role]

    total_contracts = len(contracts)
    total_links = len(links)

    # Count by type
    by_type: dict[str, int] = {}
    for c in contracts:
        ct = c.get("contract_type", "unknown")
        by_type[ct] = by_type.get(ct, 0) + 1

    # Paginate contracts only (links are typically small enough)
    contracts_page = contracts[offset : offset + limit]

    return WorkspaceContractsResponse(
        contracts=[
            WorkspaceContractEntry(
                contract_id=c.get("contract_id", ""),
                contract_type=c.get("contract_type", ""),
                role=c.get("role", ""),
                repo=c.get("repo", ""),
                file_path=c.get("file_path", ""),
                symbol_name=c.get("symbol_name", ""),
                confidence=c.get("confidence", 0.0),
                service=c.get("service"),
            )
            for c in contracts_page
        ],
        links=[
            WorkspaceContractLinkEntry(
                contract_id=lk.get("contract_id", ""),
                contract_type=lk.get("contract_type", ""),
                match_type=lk.get("match_type", "exact"),
                confidence=lk.get("confidence", 0.0),
                provider_repo=lk.get("provider_repo", ""),
                provider_file=lk.get("provider_file", ""),
                provider_symbol=lk.get("provider_symbol", ""),
                consumer_repo=lk.get("consumer_repo", ""),
                consumer_file=lk.get("consumer_file", ""),
                consumer_symbol=lk.get("consumer_symbol", ""),
            )
            for lk in links
        ],
        total_contracts=total_contracts,
        total_links=total_links,
        by_type=by_type,
    )


# ---------------------------------------------------------------------------
# GET /api/workspace/co-changes
# ---------------------------------------------------------------------------


@router.get("/co-changes", response_model=WorkspaceCoChangesResponse)
async def get_co_changes(
    ws_config=Depends(get_workspace_config),
    enricher=Depends(get_cross_repo_enricher),
    repo: str | None = Query(None, description="Filter by repo alias"),
    min_strength: float = Query(0.0, ge=0.0, le=1.0),
    limit: int = Query(50, ge=1, le=500),
):
    """Cross-repo co-change pairs, optionally filtered by repo and strength."""
    _require_workspace(ws_config)

    if enricher is None:
        return WorkspaceCoChangesResponse(co_changes=[], total=0)

    co_changes = list(getattr(enricher, "_co_changes", []))

    if repo:
        co_changes = [
            cc for cc in co_changes
            if cc.get("source_repo") == repo or cc.get("target_repo") == repo
        ]
    if min_strength > 0:
        co_changes = [cc for cc in co_changes if cc.get("strength", 0) >= min_strength]

    # Sort by strength descending
    co_changes.sort(key=lambda cc: -cc.get("strength", 0))

    total = len(co_changes)
    co_changes = co_changes[:limit]

    return WorkspaceCoChangesResponse(
        co_changes=[
            WorkspaceCoChangeEntry(
                source_repo=cc.get("source_repo", ""),
                source_file=cc.get("source_file", ""),
                target_repo=cc.get("target_repo", ""),
                target_file=cc.get("target_file", ""),
                strength=cc.get("strength", 0.0),
                frequency=cc.get("frequency", 0),
                last_date=cc.get("last_date", ""),
            )
            for cc in co_changes
        ],
        total=total,
    )
