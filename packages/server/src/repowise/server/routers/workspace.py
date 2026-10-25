"""/api/workspace — Workspace intelligence endpoints.

Exposes workspace metadata, cross-repo co-changes, and API contract data
through REST. All data is read from ``app.state`` (populated at server
startup from ``.repowise-workspace/`` JSON files) — no DB access needed.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from repowise.core.workspace import reads
from repowise.core.workspace.registry import repo_db_path
from repowise.core.workspace.repo_stats import member_stats, query_repo_stats
from repowise.core.workspace.store import read_member_stats, store_path
from repowise.server.deps import (
    get_cross_repo_enricher,
    get_workspace_config,
    resolve_session_factory,
    verify_api_key,
)
from repowise.server.schemas import (
    WorkspaceArchitectureResponse,
    WorkspaceBlastRadiusResponse,
    WorkspaceBreakingChangesResponse,
    WorkspaceCoChangesResponse,
    WorkspaceCoChangeStructure,
    WorkspaceConformanceResponse,
    WorkspaceContractDetail,
    WorkspaceContractsResponse,
    WorkspaceContractSummary,
    WorkspaceCrossRepoSummary,
    WorkspaceEstateResponse,
    WorkspaceExtractionDiagnostics,
    WorkspaceGraphEdge,
    WorkspaceGraphNode,
    WorkspaceGraphResponse,
    WorkspaceRepoEntry,
    WorkspaceRepoRemovedResponse,
    WorkspaceResponse,
    WorkspaceSyncResponse,
    WorkspaceSystemGraphMapResponse,
    WorkspaceSystemGraphResponse,
    WorkspaceTestImpactResponse,
)
from repowise.server.schemas.actions import (
    WorkspaceActionsResponse,
    WorkspaceCrossRepoAction,
    WorkspaceRepoActions,
)
from repowise.server.schemas.workspace import WorkspaceLinksBetweenResponse

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


def _member_stats(ws_config, ws_root: Path, read=query_repo_stats) -> dict[str, dict]:
    """``alias -> member stats`` for every configured repo: the workspace
    store's row when it is current, else *read* of the repo's own ``wiki.db``.
    Blocking (stats and SQLite), so callers run it in a worker thread."""
    wanted = {r.alias: (ws_root / r.path).resolve() for r in ws_config.repos}
    stored = read_member_stats(store_path(ws_root), wanted)
    return {
        alias: stored[alias] if alias in stored else read(repo_db_path(repo))
        for alias, repo in wanted.items()
    }


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

    all_stats = (
        await asyncio.to_thread(_member_stats, ws_config, ws_root_path) if ws_root_path else {}
    )
    repo_entries = []
    for r in ws_config.repos:
        stats = dict(all_stats.get(r.alias, {}))
        stats.pop("top_language", None)
        # The member's own store first: a repo updated on its own leaves the
        # config's time behind.
        indexed_at = stats.pop("indexed_at", r.indexed_at)
        repo_entries.append(
            WorkspaceRepoEntry(
                alias=r.alias,
                path=r.path,
                is_primary=r.is_primary,
                indexed_at=indexed_at,
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
    contract_type: str | None = Query(
        None, description="Filter: http, grpc, socket, topic, or data"
    ),
    repo: str | None = Query(None, description="Filter by repo alias"),
    role: str | None = Query(None, description="Filter: provider or consumer"),
    q: str | None = Query(
        None,
        description="Case-insensitive search over id, file, symbol, repo and service. "
        "Every whitespace-separated term must match.",
    ),
    linked: bool | None = Query(
        None, description="true: only contracts on a matched link; false: only those on none"
    ),
    include_links: bool = Query(
        True,
        description="false omits the link rows (total_links is still counted), for a caller "
        "paging the contract list that already holds the links",
    ),
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

    return WorkspaceContractsResponse(
        **reads.list_contracts(
            enricher.contracts,
            enricher.contract_links,
            contract_type=contract_type,
            repo=repo,
            role=role,
            q=q,
            linked=linked,
            include_links=include_links,
            limit=limit,
            offset=offset,
        )
    )


# ---------------------------------------------------------------------------
# GET /api/workspace/contracts/detail
# ---------------------------------------------------------------------------


@router.get("/contracts/detail", response_model=WorkspaceContractDetail)
async def get_contract_detail(
    ws_config=Depends(get_workspace_config),
    enricher=Depends(get_cross_repo_enricher),
    repo: str = Query(..., description="Repo alias the contract was extracted from"),
    file: str = Query(..., description="File path, relative to that repo's root"),
    id: str = Query(..., description="Contract id, e.g. 'http::GET::/api/users'"),
):
    """One contract with its schema, its links, and its unmatched reason.

    Keyed by query parameters rather than a path segment because ``file`` is a
    path and carries slashes. All three are required: ``id`` alone is not
    unique, several repos declare the same ``http::GET::/user``.

    The triple is the shareable identity, not a primary key. One file may call
    the same endpoint from two lines; when it does, the first row wins. The link
    list is filtered by the same triple, so it is identical whichever row wins;
    the enclosing symbol is not, so the response names one of the call sites.
    Keying on the line instead would rot every saved link on an edit above the
    call.
    """
    _require_workspace(ws_config)

    if enricher is None:
        raise HTTPException(status_code=404, detail="No contract data for this workspace")

    detail = reads.contract_detail(
        enricher.contracts,
        enricher.contract_links,
        enricher.get_diagnostics(),
        repo=repo,
        file_path=file,
        contract_id=id,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    return WorkspaceContractDetail(**detail)


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
        return WorkspaceCoChangesResponse(co_changes=[], total=0, total_mined=0)

    return WorkspaceCoChangesResponse(
        **reads.list_co_changes(
            enricher.co_changes,
            enricher.total_co_changes,
            repo=repo,
            min_strength=min_strength,
            limit=limit,
        )
    )


@router.get("/co-changes/structure", response_model=WorkspaceCoChangeStructure)
async def get_co_change_structure(
    ws_config=Depends(get_workspace_config),
    enricher=Depends(get_cross_repo_enricher),
    source_repo: str = Query(...),
    source_file: str = Query(...),
    target_repo: str = Query(...),
    target_file: str = Query(...),
):
    """Declared structure behind one co-changing pair: the contract links between
    the two files, and between their repositories through any files.

    Served per pair so the co-change drawer never downloads the whole link list.
    """
    _require_workspace(ws_config)
    if enricher is None:
        return WorkspaceCoChangeStructure(
            pair_links=[],
            repo_links_total=0,
            repo_links_by_type={},
            source_file_links=0,
            target_file_links=0,
        )

    return WorkspaceCoChangeStructure(
        **reads.co_change_structure(
            enricher.contract_links,
            source_repo=source_repo,
            source_file=source_file,
            target_repo=target_repo,
            target_file=target_file,
        )
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
    all_stats = (
        await asyncio.to_thread(_member_stats, ws_config, ws_root_path, member_stats) if ws_root_path else {}
    )
    repo_id_map: dict[str, str] = {}  # alias → repo_id
    nodes: list[WorkspaceGraphNode] = []
    for r in ws_config.repos:
        stats = all_stats.get(r.alias, {})
        top_language = stats.get("top_language", "unknown")
        rid = stats.get("repo_id") or r.alias
        repo_id_map[r.alias] = rid
        nodes.append(
            WorkspaceGraphNode(
                repo_id=rid,
                name=r.alias,
                file_count=stats.get("file_count", 0),
                coverage_pct=stats.get("doc_coverage_pct", 0.0),
                health_score=stats.get("health_score"),
                top_language=top_language,
            )
        )

    # Build edges
    edges: list[WorkspaceGraphEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()

    if enricher is not None:
        # Contract-based edges: each link connects two repos
        for lk in enricher.contract_links:
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
        pair_strengths: dict[tuple[str, str], list[float]] = {}
        for cc in enricher.co_changes:
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


@router.get(
    "/system-graph",
    response_model=WorkspaceSystemGraphResponse | WorkspaceSystemGraphMapResponse,
)
async def get_system_graph(
    ws_config=Depends(get_workspace_config),
    enricher=Depends(get_cross_repo_enricher),
    view: Literal["map"] | None = Query(
        None,
        description=(
            "`map`: the map's lean view, without diagnostics or structural edge "
            "refs and with `ref_count` on every edge"
        ),
    ),
):
    """Service-granular system graph: typed service nodes + directed edges.

    Read straight from the ``system_graph.json`` artifact built during workspace
    update. Edge direction is uniform — ``source`` depends on / calls ``target``.
    Returns an empty graph (not 404) when no graph has been built yet.
    ``view=map`` returns :func:`lean_system_graph` of it instead.
    """
    _require_workspace(ws_config)

    graph = enricher.get_system_graph() if enricher is not None else None
    if view == "map":
        from repowise.core.workspace.system_map_view import lean_system_graph

        return WorkspaceSystemGraphMapResponse(**lean_system_graph(graph or {}))
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
# GET /api/workspace/blast-radius
# ---------------------------------------------------------------------------


@router.get("/blast-radius", response_model=WorkspaceBlastRadiusResponse)
async def get_blast_radius(
    ws_config=Depends(get_workspace_config),
    enricher=Depends(get_cross_repo_enricher),
    target: list[str] = Query(
        ...,
        description="One or more node ids ('repo' or 'repo::service/path') or repo aliases.",
    ),
    max_depth: int = Query(3, ge=1, le=8, description="Reachability depth."),
    include_behavioral: bool = Query(
        True, description="Include co-change (behavioral) edges in reachability."
    ),
):
    """Cross-repo blast radius — downstream services impacted by a change.

    Traverses the system graph against its edge direction (a consumer→provider
    edge means changing the provider impacts the consumer), ranking impacted
    services by an impact score that weights structural dependencies above
    behavioral co-change. Reads the same ``system_graph.json`` artifact the map
    renders. Returns an empty result (not 404) when no graph is built yet.
    """
    _require_workspace(ws_config)

    from repowise.core.workspace.blast_radius import cross_repo_blast_radius
    from repowise.core.workspace.system_graph import SystemGraph

    raw = enricher.get_system_graph() if enricher is not None else None
    if not raw:
        return WorkspaceBlastRadiusResponse(targets=[], unresolved_targets=list(target))

    graph = SystemGraph.from_dict(raw)
    result = cross_repo_blast_radius(
        graph, target, max_depth=max_depth, include_behavioral=include_behavioral
    )
    return WorkspaceBlastRadiusResponse(**result.to_dict())


# ---------------------------------------------------------------------------
# GET /api/workspace/system-graph/links
# ---------------------------------------------------------------------------


@router.get("/system-graph/links", response_model=WorkspaceLinksBetweenResponse)
async def get_links_between(
    ws_config=Depends(get_workspace_config),
    enricher=Depends(get_cross_repo_enricher),
    a: str = Query(..., description="A service node id ('repo' or 'repo::service/path')."),
    b: str = Query(..., description="The other service node id."),
    kind: str | None = Query(
        None, description="One edge kind: http, grpc, event, db, package, co_change..."
    ),
    q: str | None = Query(
        None,
        description="Every whitespace-separated term must match the row's ids, files, symbols or packages.",
    ),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Everything behind the edges between two services, both directions, paged.

    Contract links, service links, package dependencies and co-changes, each
    end resolved to its service the way the system graph was built, so a
    pair's rows add up to its edges' weights. The graph, contracts and
    cross-repo artifacts are read as one build; ``stale_graph`` flags a graph
    whose repo heads differ from the contracts'. 404 when either id is not a
    node of the graph (or no graph is built).
    """
    _require_workspace(ws_config)

    from repowise.core.workspace.links_between import links_between

    index = enricher.links_index() if enricher is not None else None
    result = (
        links_between(index, a=a, b=b, kind=kind, q=q, limit=limit, offset=offset)
        if index is not None
        else None
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Service not found in the system graph")
    return WorkspaceLinksBetweenResponse(**result)


# ---------------------------------------------------------------------------
# GET /api/workspace/test-impact
# ---------------------------------------------------------------------------


@router.get("/test-impact", response_model=WorkspaceTestImpactResponse)
async def get_test_impact(
    ws_config=Depends(get_workspace_config),
    enricher=Depends(get_cross_repo_enricher),
    repo: str = Query(..., description="Provider repo alias the changed files belong to."),
    file: list[str] = Query(
        ..., description="Changed file path, relative to that repo's root. Repeatable."
    ),
):
    """Which tests in the consumer repos guard a change to these provider files.

    Rows carry the basis they came from: ``measured`` when a coverage map
    recorded the test against the consumer call site, ``inferred`` when the
    consumer's call or import graph reaches it. A link the join could not
    follow is reported in ``unresolved`` with its reason, never dropped, so an
    empty answer always names the state that produced it.

    Shares :func:`cross_repo_tests` with the MCP tools, so the REST answer and
    the agent answer cannot drift apart.
    """
    _require_workspace(ws_config)

    if enricher is None:
        return WorkspaceTestImpactResponse(summary={"reason": "no_contract_data"})

    # Imported here, not at module scope: the helper pulls in the cross-repo
    # join and holds consumer indexes open, and a single-repo server never
    # reaches this route.
    from repowise.server.mcp_server._test_impact import cross_repo_tests

    result = await cross_repo_tests(repo, list(file))
    if result is None:
        return WorkspaceTestImpactResponse(summary={"reason": "no_contract_data"})

    from repowise.core.workspace.test_impact import workspace_test_impact_to_dict

    return WorkspaceTestImpactResponse(**workspace_test_impact_to_dict(result))


# ---------------------------------------------------------------------------
# GET /api/workspace/actions
# ---------------------------------------------------------------------------

#: Per repository, per horizon: enough to say what leads, not a second copy of
#: each repository's own list.
WORKSPACE_ACTIONS_PER_REPO = 2


@router.get("/actions", response_model=WorkspaceActionsResponse)
async def get_workspace_actions(
    request: Request,
    ws_config=Depends(get_workspace_config),
    enricher=Depends(get_cross_repo_enricher),
) -> WorkspaceActionsResponse:
    """What to do next across the workspace: each repository's lead actions,
    plus the cross-repository ones only the workspace can see.

    Each repository's list is the same fold its own Overview serves
    (``load_actions_view``), trimmed to the work tiers, so the two pages never
    disagree about what leads.
    """
    from repowise.core.persistence.crud.analysis.actions import load_actions_view
    from repowise.core.persistence.database import get_session

    _require_workspace(ws_config)
    ws_root = getattr(request.app.state, "workspace_root", None)
    ws_root_path = Path(ws_root) if ws_root else None

    all_stats = (
        await asyncio.to_thread(_member_stats, ws_config, ws_root_path) if ws_root_path else {}
    )
    repos: list[WorkspaceRepoActions] = []
    for r in ws_config.repos:
        repo_id = all_stats.get(r.alias, {}).get("repo_id")
        if not repo_id:
            repos.append(
                WorkspaceRepoActions(
                    alias=r.alias, repo_id=None, status="unavailable", reason="Not indexed yet."
                )
            )
            continue
        try:
            async with get_session(resolve_session_factory(request.app.state, repo_id)) as session:
                view = await load_actions_view(
                    session,
                    repo_id,
                    lookup=getattr(request.app.state, "cross_repo_enricher", None),
                    alias=r.alias,
                )
        except Exception as exc:  # one repository must not cost the page
            _log.warning("workspace actions: %s unavailable: %s", r.alias, exc)
            repos.append(
                WorkspaceRepoActions(
                    alias=r.alias,
                    repo_id=repo_id,
                    status="unavailable",
                    reason="Could not read this repository's index.",
                )
            )
            continue
        horizons = {}
        for key, h in view["horizons"].items():
            work = [a for a in h["actions"] if a["tier"] != "improve_signal"]
            horizons[key] = {
                **h,
                "actions": work[:WORKSPACE_ACTIONS_PER_REPO],
                "total": (h["by_tier"].get("act_now", 0) + h["by_tier"].get("plan", 0)),
            }
        repos.append(
            WorkspaceRepoActions(
                alias=r.alias, repo_id=repo_id, status="available", horizons=horizons
            )
        )

    cross_repo: list[WorkspaceCrossRepoAction] = []
    report = enricher.get_breaking_changes() if enricher is not None else None
    if report:
        view = reads.breaking_changes_view(report, repo=None, severity="breaking")
        count = int(view.get("breaking_count") or 0)
        if count:
            impacted = sorted(view.get("impacted_repos") or [])
            cross_repo.append(
                WorkspaceCrossRepoAction(
                    kind="breaking_contract",
                    title=f"Resolve {count} breaking contract change{'' if count == 1 else 's'}",
                    impact=(
                        "A provider changed a route, topic or table that another repository "
                        "consumes"
                        + (f"; {', '.join(impacted)} depend on it." if impacted else ".")
                    ),
                    count=count,
                    repos=impacted,
                )
            )
    return WorkspaceActionsResponse(repos=repos, cross_repo=cross_repo)


# ---------------------------------------------------------------------------
# GET /api/workspace/breaking-changes
# ---------------------------------------------------------------------------


@router.get("/breaking-changes", response_model=WorkspaceBreakingChangesResponse)
async def get_breaking_changes(
    ws_config=Depends(get_workspace_config),
    enricher=Depends(get_cross_repo_enricher),
    repo: str | None = Query(None, description="Filter to changes whose provider is in this repo."),
    severity: str | None = Query(None, description="Filter: breaking or warning."),
):
    """Provider contract findings and directly linked consumer exposure across repos.

    Computed during the most recent ``repowise update --workspace`` by diffing the
    freshly-extracted contracts against the previously-indexed set, then resolving
    each finding's direct consumers from the matched links. Returns an empty report
    (not 404) when no findings were detected or no report exists yet; ``generated_at``
    distinguishes those states.
    """
    _require_workspace(ws_config)

    report = enricher.get_breaking_changes() if enricher is not None else None
    if not report:
        return WorkspaceBreakingChangesResponse()
    return WorkspaceBreakingChangesResponse(
        **reads.breaking_changes_view(report, repo=repo, severity=severity)
    )


# ---------------------------------------------------------------------------
# GET /api/workspace/conformance
# ---------------------------------------------------------------------------


@router.get("/conformance", response_model=WorkspaceConformanceResponse)
async def get_conformance(
    ws_config=Depends(get_workspace_config),
    enricher=Depends(get_cross_repo_enricher),
    repo: str | None = Query(
        None, description="Filter to violations/cycles that involve this repo alias."
    ),
):
    """Architecture conformance — declared dependency-rule violations + cycles.

    Computed during the most recent ``repowise update --workspace`` by checking
    the workspace's declared allow/deny rules against the system graph and
    detecting circular service dependencies. Returns an empty report (not 404)
    when no rules are declared, no findings exist, or no report has been built.
    """
    _require_workspace(ws_config)

    report = enricher.get_conformance() if enricher is not None else None
    if not report:
        return WorkspaceConformanceResponse()
    return WorkspaceConformanceResponse(**reads.conformance_view(report, repo=repo))


# ---------------------------------------------------------------------------
# GET /api/workspace/architecture
# ---------------------------------------------------------------------------


@router.get("/architecture", response_model=WorkspaceArchitectureResponse)
async def get_architecture(
    ws_config=Depends(get_workspace_config),
    enricher=Depends(get_cross_repo_enricher),
):
    """Architecture-complexity metrics — propagation cost, core, and a 1-10 score.

    Computed at request time from the same ``system_graph.json`` artifact the map
    renders, using structural edges only (co-change is excluded). The declared-rule
    violation count, if a conformance report exists, is folded into the score.
    Deterministic and LLM-free. Returns empty metrics (not 404) when no graph is
    built yet.
    """
    _require_workspace(ws_config)

    from repowise.core.workspace.architecture_metrics import compute_architecture_metrics
    from repowise.core.workspace.system_graph import SystemGraph

    raw = enricher.get_system_graph() if enricher is not None else None
    if not raw:
        return WorkspaceArchitectureResponse()

    graph = SystemGraph.from_dict(raw)

    # Fold in the conformance violation count when a report exists — the metric is
    # otherwise independent of rule evaluation.
    conformance = enricher.get_conformance() if enricher is not None else None
    violations = int(conformance.get("violation_count", 0)) if conformance else 0

    metrics = compute_architecture_metrics(
        graph,
        conformance_violations=violations,
        generated_at=raw.get("generated_at", ""),
    )
    return WorkspaceArchitectureResponse(**metrics.to_dict())


# ---------------------------------------------------------------------------
# GET /api/workspace/estate
# ---------------------------------------------------------------------------


@router.get("/estate", response_model=WorkspaceEstateResponse)
async def get_estate(
    request: Request,
    ws_config=Depends(get_workspace_config),
) -> WorkspaceEstateResponse:
    """The estate report ``repowise report estate --format json`` prints.

    Built on request by the same core function the CLI calls, from the stored
    workspace artifacts and each member's index; nothing is extracted again.
    Opens every member index, so it costs about 0.1 to 0.3 s per repo once the
    server is warm. Without a stored system graph the graph sections come back
    inconclusive and say which command builds it.
    """
    _require_workspace(ws_config)

    from repowise.core.report.estate import build_estate_document

    ws_root = getattr(request.app.state, "workspace_root", None)
    _require_workspace(ws_root)
    return WorkspaceEstateResponse(**await build_estate_document(Path(ws_root)))


# ---------------------------------------------------------------------------
# POST /api/workspace/sync
# ---------------------------------------------------------------------------


@router.post("/sync", response_model=WorkspaceSyncResponse, status_code=202)
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
    from repowise.server.schemas import WorkspaceSyncResult

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

        # Not indexed yet → establish the repo-local database and run the
        # first full index through the same job machinery, instead of
        # bouncing the user to the CLI.
        if not db_path.exists():
            try:
                from repowise.server.repo_db import ensure_repo_registration
                from repowise.server.routers.repos import _enqueue_index_job

                factory, new_repo_id = await ensure_repo_registration(
                    request.app.state,
                    local_path=str(repo_path),
                    name=entry.alias,
                )
                job_id = await _enqueue_index_job(request, factory, new_repo_id)
            except Exception as exc:
                results.append(
                    WorkspaceSyncResult(
                        alias=entry.alias,
                        status="error",
                        reason=f"first-time index failed to start: {exc}",
                    )
                )
                continue
            results.append(
                WorkspaceSyncResult(
                    alias=entry.alias,
                    repo_id=new_repo_id,
                    status="accepted" if job_id else "skipped",
                    reason=None if job_id else "a job is already running for this repo",
                    job_id=job_id,
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

        async def _create_job(
            *,
            session_factory=session_factory,
            repo_id: str | None = repo_id,
        ) -> tuple[str | None, str | None]:
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


# ---------------------------------------------------------------------------
# DELETE /api/workspace/repos/{alias}
# ---------------------------------------------------------------------------


@router.delete(
    "/repos/{alias}",
    response_model=WorkspaceRepoRemovedResponse,
    status_code=200,
)
async def remove_workspace_repo(
    alias: str,
    request: Request,
    ws_config=Depends(get_workspace_config),
):
    """Remove a repository from the workspace configuration.

    Drops the repo entry from ``.repowise-workspace.yaml`` and cleans up
    running server state (session factories, FTS, repo id mappings) so
    the change takes effect immediately without requiring a restart.
    """
    _require_workspace(ws_config)

    ws_root = getattr(request.app.state, "workspace_root", None)
    if ws_root is None:
        raise HTTPException(status_code=500, detail="Workspace root missing on app state")
    ws_root_path = Path(ws_root)

    entry = ws_config.get_repo(alias)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown repo alias '{alias}' in workspace.",
        )

    # Compute absolute repo path to clean up mappings
    repo_path_str = str((ws_root_path / entry.path).resolve())

    # Remove from config and save to disk
    removed = ws_config.remove_repo(alias)
    if removed is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown repo alias '{alias}' in workspace.",
        )
    ws_config.save(ws_root_path)

    # Update live in-memory app state
    request.app.state.workspace_config = ws_config

    # Clean up associated in-memory references if present
    path_to_rid = getattr(request.app.state, "workspace_path_to_repo_id", None)
    repo_id = None
    if path_to_rid and repo_path_str in path_to_rid:
        repo_id = path_to_rid.pop(repo_path_str)

    if repo_id is not None:
        ws_sessions = getattr(request.app.state, "workspace_sessions", None)
        if ws_sessions and repo_id in ws_sessions:
            ws_sessions.pop(repo_id, None)

        ws_fts = getattr(request.app.state, "workspace_fts", None)
        if ws_fts and repo_id in ws_fts:
            ws_fts.pop(repo_id, None)

        ws_vs = getattr(request.app.state, "workspace_vector_stores", None)
        if ws_vs and repo_id in ws_vs:
            ws_vs.pop(repo_id, None)

    return WorkspaceRepoRemovedResponse(
        ok=True,
        alias=alias,
        remaining_repos=len(ws_config.repos),
    )

