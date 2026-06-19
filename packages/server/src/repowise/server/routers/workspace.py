"""/api/workspace — Workspace intelligence endpoints.

Exposes workspace metadata, cross-repo co-changes, and API contract data
through REST. All data is read from ``app.state`` (populated at server
startup from ``.repowise-workspace/`` JSON files) — no DB access needed.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from repowise.server.deps import (
    get_cross_repo_enricher,
    get_workspace_config,
    resolve_session_factory,
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
    WorkspaceExtractionDiagnostics,
    WorkspaceGraphEdge,
    WorkspaceGraphNode,
    WorkspaceGraphResponse,
    WorkspaceRepoEntry,
    WorkspaceResponse,
    WorkspaceSystemGraphResponse,
)
from repowise.server.services.module_health import read_repo_health_score

router = APIRouter(
    prefix="/api/workspace",
    tags=["workspace"],
    dependencies=[Depends(verify_api_key)],
)


_log = logging.getLogger("repowise.server.routers.workspace")


def _require_workspace(ws_config: object) -> None:
    """Raise 404 if not in workspace mode."""
    if ws_config is None:
        raise HTTPException(status_code=404, detail="Not running in workspace mode")


def _query_top_language(db_path: Path) -> str:
    """Return the most common language across graph_nodes in a repo's wiki.db."""
    if not db_path.exists():
        return "unknown"
    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT language, COUNT(*) AS cnt FROM graph_nodes "
                "WHERE language IS NOT NULL AND language != '' "
                "GROUP BY language ORDER BY cnt DESC LIMIT 1"
            ).fetchone()
            return row[0] if row else "unknown"
    except Exception:
        return "unknown"


def _compute_health_score(file_count: int, doc_coverage_pct: float, hotspot_count: int) -> int:
    """Derive a 0-100 health score from available repo metrics."""
    if file_count == 0:
        return 0
    coverage_component = doc_coverage_pct * 0.6
    hotspot_ratio = min(hotspot_count / max(file_count, 1), 1.0)
    hotspot_component = (1.0 - hotspot_ratio) * 100 * 0.4
    return max(0, min(100, round(coverage_component + hotspot_component)))


def _resolve_graph_health_score(db_path: Path, stats: dict) -> tuple[float, str]:
    canonical = read_repo_health_score(db_path)
    if canonical is not None:
        return canonical, "canonical"
    return (
        float(
            _compute_health_score(
                stats.get("file_count", 0),
                stats.get("doc_coverage_pct", 0.0),
                stats.get("hotspot_count", 0),
            )
        ),
        "derived",
    )


def _query_repo_stats(db_path: Path) -> dict:
    """Query basic stats from a repo's wiki.db using raw sqlite3.

    Returns a dict with repo_id, file_count, symbol_count, page_count,
    doc_coverage_pct, hotspot_count, status, docs_enabled, and
    docs_skip_reason.  All values default to sensible neutrals on error.
    """
    result: dict = {
        "repo_id": None,
        "file_count": 0,
        "symbol_count": 0,
        "page_count": 0,
        "doc_coverage_pct": 0.0,
        "hotspot_count": 0,
        "status": "needs_index",
        "docs_enabled": False,
        "docs_skip_reason": None,
    }
    # Surface docs lifecycle from state.json so the UI shows a coherent
    # picture even when only indexing (no docs) ran.
    try:
        import json as _json

        state_path = db_path.parent / "state.json"
        if state_path.is_file():
            state = _json.loads(state_path.read_text(encoding="utf-8"))
            result["docs_enabled"] = bool(state.get("docs_enabled", False))
            result["docs_skip_reason"] = state.get("docs_skip_reason")
    except Exception:
        pass
    if not db_path.exists():
        if not db_path.parent.parent.is_dir():
            result["status"] = "missing_dir"
        return result
    result["status"] = "indexed"
    try:
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()

        # repo id
        row = c.execute("SELECT id FROM repositories LIMIT 1").fetchone()
        if row:
            result["repo_id"] = row[0]

        # file count (graph_nodes)
        row = c.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()
        result["file_count"] = row[0] if row else 0

        # symbol count
        row = c.execute("SELECT COALESCE(SUM(symbol_count), 0) FROM graph_nodes").fetchone()
        result["symbol_count"] = int(row[0]) if row else 0

        # page count
        row = c.execute("SELECT COUNT(*) FROM wiki_pages").fetchone()
        result["page_count"] = row[0] if row else 0

        # doc coverage (avg confidence * 100)
        row = c.execute("SELECT AVG(confidence) FROM wiki_pages").fetchone()
        result["doc_coverage_pct"] = round(float(row[0] or 0.0) * 100, 1)

        # hotspot count — use the canonical is_hotspot flag, matching the rest
        # of the codebase (module_health, extract-demo-data). The earlier
        # churn_percentile >= 90 predicate never matched: churn_percentile is
        # stored on a 0.0-1.0 scale and only scaled to 0-100 at the API layer.
        try:
            row = c.execute("SELECT COUNT(*) FROM git_metadata WHERE is_hotspot = 1").fetchone()
            result["hotspot_count"] = row[0] if row else 0
        except sqlite3.OperationalError:
            pass  # table or column may not exist

        conn.close()
    except Exception:
        _log.debug("Failed to query stats from %s", db_path, exc_info=True)
    return result


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

    ws_root = getattr(request.app.state, "workspace_root", None)
    ws_root_path = Path(ws_root) if ws_root else None

    repo_entries = []
    for r in ws_config.repos:
        stats: dict = {}
        if ws_root_path:
            repo_path = (ws_root_path / r.path).resolve()
            db_path = repo_path / ".repowise" / "wiki.db"
            stats = _query_repo_stats(db_path)
        repo_entries.append(
            WorkspaceRepoEntry(
                alias=r.alias,
                path=r.path,
                is_primary=r.is_primary,
                indexed_at=r.indexed_at,
                last_commit_at_index=r.last_commit_at_index,
                **stats,
            )
        )

    cross_repo_summary = None
    contract_summary = None

    if enricher is not None:
        summary = enricher.get_cross_repo_summary()
        cross_repo_summary = WorkspaceCrossRepoSummary(**summary)
        if enricher.has_contract_data:
            cs = enricher.get_contract_summary()
            contract_summary = WorkspaceContractSummary(**cs)

    return WorkspaceResponse(
        is_workspace=True,
        workspace_root=ws_root,
        workspace_name=Path(ws_root_path).name if ws_root_path else None,
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
            contracts=[],
            links=[],
            total_contracts=0,
            total_links=0,
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
            lk for lk in links if lk.get("provider_repo") == repo or lk.get("consumer_repo") == repo
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
            cc
            for cc in co_changes
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


# ---------------------------------------------------------------------------
# GET /api/workspace/graph
# ---------------------------------------------------------------------------


@router.get("/graph", response_model=WorkspaceGraphResponse)
async def get_workspace_graph(
    request: Request,
    ws_config=Depends(get_workspace_config),
    enricher=Depends(get_cross_repo_enricher),
):
    """Cross-repo graph: repos as mega-nodes, contracts/co-changes as edges."""
    _require_workspace(ws_config)

    ws_root = getattr(request.app.state, "workspace_root", None)
    ws_root_path = Path(ws_root) if ws_root else None

    # Build nodes from repo metadata
    repo_id_map: dict[str, str] = {}  # alias → repo_id
    nodes: list[WorkspaceGraphNode] = []
    for r in ws_config.repos:
        stats: dict = {}
        top_language = "unknown"
        if ws_root_path:
            repo_path = (ws_root_path / r.path).resolve()
            db_path = repo_path / ".repowise" / "wiki.db"
            stats = _query_repo_stats(db_path)
            top_language = _query_top_language(db_path)
            health_score, health_score_source = _resolve_graph_health_score(db_path, stats)
        else:
            health_score = 0.0
            health_score_source = "derived"
        rid = stats.get("repo_id") or r.alias
        repo_id_map[r.alias] = rid
        nodes.append(
            WorkspaceGraphNode(
                repo_id=rid,
                name=r.alias,
                file_count=stats.get("file_count", 0),
                coverage_pct=stats.get("doc_coverage_pct", 0.0),
                health_score=health_score,
                health_score_source=health_score_source,
                top_language=top_language,
            )
        )

    # Build edges
    edges: list[WorkspaceGraphEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()

    if enricher is not None:
        # Contract-based edges: each link connects two repos
        links = list(getattr(enricher, "_contract_links", []))
        for lk in links:
            p_repo = lk.get("provider_repo", "")
            c_repo = lk.get("consumer_repo", "")
            if not p_repo or not c_repo or p_repo == c_repo:
                continue
            p_id = repo_id_map.get(p_repo, p_repo)
            c_id = repo_id_map.get(c_repo, c_repo)
            key = (min(p_id, c_id), max(p_id, c_id), "contract")
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append(
                WorkspaceGraphEdge(
                    source=p_id,
                    target=c_id,
                    type="contract",
                    strength=lk.get("confidence", 0.8),
                    label=lk.get("contract_type"),
                )
            )

        # Co-change-based edges: aggregate per repo-pair
        co_changes = list(getattr(enricher, "_co_changes", []))
        pair_strengths: dict[tuple[str, str], list[float]] = {}
        for cc in co_changes:
            s_repo = cc.get("source_repo", "")
            t_repo = cc.get("target_repo", "")
            if not s_repo or not t_repo or s_repo == t_repo:
                continue
            s_id = repo_id_map.get(s_repo, s_repo)
            t_id = repo_id_map.get(t_repo, t_repo)
            pair_key = (min(s_id, t_id), max(s_id, t_id))
            pair_strengths.setdefault(pair_key, []).append(cc.get("strength", 0.0))

        for (src, tgt), strengths in pair_strengths.items():
            key = (src, tgt, "co_change")
            if key in seen_edges:
                continue
            seen_edges.add(key)
            avg_strength = sum(strengths) / len(strengths)
            edges.append(
                WorkspaceGraphEdge(
                    source=src,
                    target=tgt,
                    type="co_change",
                    strength=round(avg_strength, 3),
                    label=f"{len(strengths)} co-changes",
                )
            )

    return WorkspaceGraphResponse(nodes=nodes, edges=edges)


# ---------------------------------------------------------------------------
# GET /api/workspace/system-graph
# ---------------------------------------------------------------------------


@router.get("/system-graph", response_model=WorkspaceSystemGraphResponse)
async def get_system_graph(
    ws_config=Depends(get_workspace_config),
    enricher=Depends(get_cross_repo_enricher),
):
    """Service-granular system graph: typed service nodes + directed edges.

    Read straight from the ``system_graph.json`` artifact built during workspace
    update. Edge direction is uniform — ``source`` depends on / calls ``target``.
    Returns an empty graph (not 404) when no graph has been built yet.
    """
    _require_workspace(ws_config)

    graph = enricher.get_system_graph() if enricher is not None else None
    if not graph:
        return WorkspaceSystemGraphResponse()
    return WorkspaceSystemGraphResponse(**graph)


# ---------------------------------------------------------------------------
# GET /api/workspace/diagnostics
# ---------------------------------------------------------------------------


@router.get("/diagnostics", response_model=WorkspaceExtractionDiagnostics)
async def get_diagnostics(
    ws_config=Depends(get_workspace_config),
    enricher=Depends(get_cross_repo_enricher),
):
    """Extraction diagnostics — why the cross-repo link count is what it is.

    Reports per-repo provider/consumer counts, unmatched consumers grouped by
    reason, orphan providers, and weak links. Sourced from the system graph
    artifact's ``diagnostics`` block.
    """
    _require_workspace(ws_config)

    diagnostics = enricher.get_diagnostics() if enricher is not None else None
    if not diagnostics:
        return WorkspaceExtractionDiagnostics()
    return WorkspaceExtractionDiagnostics(**diagnostics)


# ---------------------------------------------------------------------------
# POST /api/workspace/sync
# ---------------------------------------------------------------------------


@router.post("/sync", status_code=202)
async def sync_workspace(
    request: Request,
    repo_alias: str | None = Query(
        None,
        description="If set, only sync this repo alias (still fans through the job system).",
    ),
    full_resync: bool = Query(False, description="Trigger a full resync instead of incremental."),
    ws_config=Depends(get_workspace_config),
):
    """Fan out a sync to every (stale or unindexed) repo in the workspace.

    Returns one :class:`WorkspaceSyncResult` per attempted repo with the
    decision (accepted / skipped / error) so the web UI can render
    progress without polling for each repo individually.

    Uses the same job machinery as ``POST /api/repos/{id}/sync`` so the
    scheduler, cost ledger, and live-progress hooks all work without
    special cases.
    """
    from repowise.server.schemas import (
        WorkspaceSyncResponse,
        WorkspaceSyncResult,
    )

    _require_workspace(ws_config)

    from sqlalchemy import select

    from repowise.core.persistence import crud
    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import GenerationJob
    from repowise.server.routers.repos import _launch_job_task

    ws_root = getattr(request.app.state, "workspace_root", None)
    if ws_root is None:
        raise HTTPException(status_code=500, detail="Workspace root missing on app state")
    ws_root_path = Path(ws_root)

    results: list[WorkspaceSyncResult] = []

    # Resolve aliases → entries to operate on.
    if repo_alias is not None:
        entry = ws_config.get_repo(repo_alias)
        if entry is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown repo alias '{repo_alias}' in workspace.",
            )
        entries = [entry]
    else:
        entries = list(ws_config.repos)

    for entry in entries:
        repo_path = (ws_root_path / entry.path).resolve()
        db_path = repo_path / ".repowise" / "wiki.db"

        # Not indexed yet → no row to write a job against. Surface this
        # as "skipped" so the UI can show a clear "run `repowise update
        # --repo <alias>` from the CLI" hint. (A future enhancement is
        # to support remote first-time indexing; see Phase C.)
        if not db_path.exists():
            results.append(
                WorkspaceSyncResult(
                    alias=entry.alias,
                    status="skipped",
                    reason="not indexed yet (run `repowise update --repo "
                    + entry.alias
                    + "` from the CLI)",
                )
            )
            continue

        # Discover repo_id from the per-repo DB.
        try:
            with sqlite3.connect(str(db_path)) as conn:
                row = conn.execute("SELECT id FROM repositories LIMIT 1").fetchone()
        except Exception as exc:
            results.append(
                WorkspaceSyncResult(
                    alias=entry.alias,
                    status="error",
                    reason=f"could not read repo id: {exc}",
                )
            )
            continue

        if not row:
            results.append(
                WorkspaceSyncResult(
                    alias=entry.alias,
                    status="error",
                    reason="repository row missing in wiki.db",
                )
            )
            continue
        repo_id = row[0]

        session_factory = resolve_session_factory(request.app.state, repo_id)

        async def _create_job() -> tuple[str | None, str | None]:
            """Create a pending job row, returning (job_id, error)."""
            try:
                async with get_session(session_factory) as session:
                    # Prevent concurrent runs on the same repo.
                    active = await session.execute(
                        select(GenerationJob.id)
                        .where(GenerationJob.repository_id == repo_id)
                        .where(GenerationJob.status.in_(["pending", "running"]))
                        .limit(1)
                    )
                    if active.scalar_one_or_none() is not None:
                        return None, "a sync is already running for this repo"

                    config_data = {"mode": "full_resync"} if full_resync else None
                    job = await crud.upsert_generation_job(
                        session,
                        repository_id=repo_id,
                        status="pending",
                        config=config_data,
                    )
                    await session.commit()
                    return job.id, None
            except Exception as exc:
                return None, str(exc)

        job_id, err = await _create_job()
        if err is not None:
            results.append(
                WorkspaceSyncResult(
                    alias=entry.alias,
                    repo_id=repo_id,
                    status="skipped" if "already running" in err else "error",
                    reason=err,
                )
            )
            continue

        _launch_job_task(request, job_id, repo_id)
        results.append(
            WorkspaceSyncResult(
                alias=entry.alias,
                repo_id=repo_id,
                job_id=job_id,
                status="accepted",
            )
        )

    return WorkspaceSyncResponse(
        results=results,
        accepted=sum(1 for r in results if r.status == "accepted"),
        skipped=sum(1 for r in results if r.status == "skipped"),
        errors=sum(1 for r in results if r.status == "error"),
    )
