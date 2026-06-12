"""/api/symbols — Symbol lookup and search."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.core.persistence.decision_graph import get_governing_decisions
from repowise.core.persistence.models import (
    GitFunctionBlame,
    GitMetadata,
    GraphNode,
    WikiSymbol,
)
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import Paginated, SymbolImportanceComponents, SymbolResponse
from repowise.server.services.symbol_ranking import (
    compute_components,
    rank_symbols,
)

router = APIRouter(
    prefix="/api/symbols",
    tags=["symbols"],
    dependencies=[Depends(verify_api_key)],
)


SortKey = Literal["importance", "name", "complexity", "kind"]


def _attach_signals(
    response: SymbolResponse,
    *,
    pagerank: float,
    is_entry_point: bool,
    churn_percentile: float | None,
    is_hotspot: bool | None,
    components,
    score: float,
) -> SymbolResponse:
    response.importance_score = round(score, 6)
    response.importance_components = SymbolImportanceComponents(
        file_pagerank=components.file_pagerank,
        visibility_factor=components.visibility_factor,
        complexity_norm=components.complexity_norm,
        kind_boost=components.kind_boost,
        is_entry_point=components.is_entry_point,
    )
    response.file_pagerank = pagerank
    response.is_entry_point = is_entry_point
    response.file_churn_percentile = churn_percentile
    response.file_is_hotspot = is_hotspot
    return response


async def _attach_blame(
    session: AsyncSession, repo_id: str, items: list[SymbolResponse]
) -> list[SymbolResponse]:
    """Join the page's symbols against git_function_blame in one query."""
    ids = [s.symbol_id for s in items]
    if not ids:
        return items
    rows = await session.execute(
        select(GitFunctionBlame).where(
            GitFunctionBlame.repository_id == repo_id,
            GitFunctionBlame.symbol_id.in_(ids),
        )
    )
    by_id = {b.symbol_id: b for b in rows.scalars().all()}
    for s in items:
        b = by_id.get(s.symbol_id)
        if b is None:
            continue
        s.blame_mod_count = b.mod_count
        s.blame_recent_mod_count = b.recent_mod_count
        s.blame_median_author_time = b.median_author_time
        s.blame_owner_name = b.owner_name
        s.blame_owner_line_pct = b.owner_line_pct
    return items


async def _file_signals(
    session: AsyncSession, repo_id: str, paths: set[str]
) -> tuple[dict[str, tuple[float, bool]], dict[str, tuple[float | None, bool | None]]]:
    """Fetch file-level signals (pagerank + entry-point, churn + hotspot) for
    the given file paths in a single round trip each."""

    if not paths:
        return {}, {}

    pagerank_rows = await session.execute(
        select(GraphNode.node_id, GraphNode.pagerank, GraphNode.is_entry_point).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_type == "file",
            GraphNode.node_id.in_(paths),
        )
    )
    pagerank_map: dict[str, tuple[float, bool]] = {
        row.node_id: (float(row.pagerank or 0.0), bool(row.is_entry_point)) for row in pagerank_rows
    }

    churn_rows = await session.execute(
        select(GitMetadata.file_path, GitMetadata.churn_percentile, GitMetadata.is_hotspot).where(
            GitMetadata.repository_id == repo_id,
            GitMetadata.file_path.in_(paths),
        )
    )
    churn_map: dict[str, tuple[float | None, bool | None]] = {
        # 0–1 stored → 0–100 exposed (matches HotspotResponse contract).
        row.file_path: (
            float(row.churn_percentile) * 100.0 if row.churn_percentile is not None else None,
            bool(row.is_hotspot),
        )
        for row in churn_rows
    }
    return pagerank_map, churn_map


@router.get("", response_model=Paginated[SymbolResponse])
async def search_symbols(
    repo_id: str = Query(..., description="Repository ID"),
    q: str = Query("", description="Search query (substring match on name)"),
    kind: str | None = Query(None, description="Filter by symbol kind"),
    language: str | None = Query(None, description="Filter by language"),
    visibility: str | None = Query(None, description="Filter by visibility"),
    file_path: str | None = Query(
        None, description="Filter by exact source file path (for hotspot drill-down)"
    ),
    in_hot_files: bool = Query(False, description="Only symbols whose file is a hotspot"),
    in_entry_points: bool = Query(False, description="Only symbols in entry-point files"),
    sort: SortKey = Query("importance", description="Sort key"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Paginated[SymbolResponse]:
    """Search symbols by name/kind/language with importance-aware ranking.

    Importance sort is stable across pages: the server scores every symbol
    that matches the filters, sorts in Python (composite score is cheap),
    and returns the requested page. For very large symbol tables (>50k) the
    score should be persisted at index time — tracked as Phase 3 work.
    """

    base = select(WikiSymbol).where(WikiSymbol.repository_id == repo_id)
    if q:
        base = base.where(WikiSymbol.name.ilike(f"%{q}%"))
    if kind:
        base = base.where(WikiSymbol.kind == kind)
    if language:
        base = base.where(WikiSymbol.language == language)
    if visibility:
        base = base.where(WikiSymbol.visibility == visibility)
    if file_path:
        base = base.where(WikiSymbol.file_path == file_path)

    # Optional file-level filters require resolving file paths up front.
    if in_hot_files or in_entry_points:
        file_query = select(GraphNode.node_id if in_entry_points else GitMetadata.file_path)
        if in_entry_points:
            file_query = file_query.where(
                GraphNode.repository_id == repo_id,
                GraphNode.node_type == "file",
                GraphNode.is_entry_point.is_(True),
            )
        if in_hot_files:
            hot_paths = await session.execute(
                select(GitMetadata.file_path).where(
                    GitMetadata.repository_id == repo_id,
                    GitMetadata.is_hotspot.is_(True),
                )
            )
            hot_set = {r.file_path for r in hot_paths}
            base = base.where(WikiSymbol.file_path.in_(hot_set or {""}))
        if in_entry_points:
            ep_paths = await session.execute(
                select(GraphNode.node_id).where(
                    GraphNode.repository_id == repo_id,
                    GraphNode.node_type == "file",
                    GraphNode.is_entry_point.is_(True),
                )
            )
            ep_set = {r.node_id for r in ep_paths}
            base = base.where(WikiSymbol.file_path.in_(ep_set or {""}))

    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0

    if sort == "importance":
        # Need the full filtered set to score then page; for non-importance
        # sorts SQL ORDER BY is faster and equally stable.
        all_rows = (await session.execute(base)).scalars().all()
        paths = {s.file_path for s in all_rows}
        pagerank_map, churn_map = await _file_signals(session, repo_id, paths)
        ranked = rank_symbols(list(all_rows), pagerank_map)
        page = ranked[offset : offset + limit]
        items: list[SymbolResponse] = []
        for r in page:
            response = SymbolResponse.from_orm(r.symbol)
            churn, hot = churn_map.get(getattr(r.symbol, "file_path", "") or "", (None, None))
            items.append(
                _attach_signals(
                    response,
                    pagerank=r.file_pagerank,
                    is_entry_point=r.is_entry_point,
                    churn_percentile=churn,
                    is_hotspot=hot,
                    components=r.components,
                    score=r.score,
                )
            )
    else:
        if sort == "name":
            base = base.order_by(WikiSymbol.name)
        elif sort == "complexity":
            base = base.order_by(WikiSymbol.complexity_estimate.desc(), WikiSymbol.name)
        elif sort == "kind":
            base = base.order_by(WikiSymbol.kind, WikiSymbol.name)
        rows = (await session.execute(base.limit(limit).offset(offset))).scalars().all()
        paths = {s.file_path for s in rows}
        pagerank_map, churn_map = await _file_signals(session, repo_id, paths)
        items = []
        for sym in rows:
            response = SymbolResponse.from_orm(sym)
            pr, ep = pagerank_map.get(sym.file_path, (0.0, False))
            churn, hot = churn_map.get(sym.file_path, (None, None))
            components = compute_components(
                file_pagerank=pr,
                visibility=sym.visibility,
                complexity=sym.complexity_estimate,
                kind=sym.kind,
                is_entry_point=ep,
            )
            items.append(
                _attach_signals(
                    response,
                    pagerank=pr,
                    is_entry_point=ep,
                    churn_percentile=churn,
                    is_hotspot=hot,
                    components=components,
                    score=components.score(),
                )
            )

    items = await _attach_blame(session, repo_id, items)
    next_offset = offset + limit if offset + limit < total else None
    return Paginated[SymbolResponse](
        items=items,
        total=total,
        has_more=next_offset is not None,
        next_offset=next_offset,
    )


@router.get("/detail")
async def symbol_detail(
    repo_id: str = Query(..., description="Repository ID"),
    symbol_id: str = Query(..., description="Symbol ID ({path}::{name})"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Everything the symbol entity page renders, in one call.

    Symbol row + function blame + callers/callees + graph metrics +
    governing decisions + parent-file context.
    """
    result = await session.execute(
        select(WikiSymbol).where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.symbol_id == symbol_id,
        )
    )
    sym = result.scalar_one_or_none()
    if sym is None:
        raise HTTPException(status_code=404, detail=f"Symbol not found: {symbol_id}")

    symbol = SymbolResponse.from_orm(sym)
    pagerank_map, churn_map = await _file_signals(session, repo_id, {sym.file_path})
    pr, ep = pagerank_map.get(sym.file_path, (0.0, False))
    components = compute_components(
        file_pagerank=pr,
        visibility=sym.visibility,
        complexity=sym.complexity_estimate,
        kind=sym.kind,
        is_entry_point=ep,
    )
    churn, hot = churn_map.get(sym.file_path, (None, None))
    symbol = _attach_signals(
        symbol,
        pagerank=pr,
        is_entry_point=ep,
        churn_percentile=churn,
        is_hotspot=hot,
        components=components,
        score=components.score(),
    )
    [symbol] = await _attach_blame(session, repo_id, [symbol])

    # Callers/callees from the symbol graph (call + heritage edges).
    callers: list[dict] = []
    callees: list[dict] = []
    node = await crud.get_graph_node(session, repo_id, symbol_id)
    if node is not None:
        edges = await crud.get_graph_edges_for_node(
            session, repo_id, symbol_id, direction="both", limit=40
        )
        other_ids = {
            e.source_node_id if e.source_node_id != symbol_id else e.target_node_id for e in edges
        }
        node_map = await crud.get_graph_nodes_by_ids(session, repo_id, list(other_ids))
        for e in edges:
            inbound = e.target_node_id == symbol_id
            other_id = e.source_node_id if inbound else e.target_node_id
            other = node_map.get(other_id)
            entry = {
                "symbol_id": other_id,
                "name": other.name
                if other and other.name
                else (other_id.split("::")[-1] if "::" in other_id else other_id),
                "kind": other.kind if other else "unknown",
                "file": other.file_path
                if other and other.file_path
                else (other_id.split("::")[0] if "::" in other_id else other_id),
                "start_line": other.start_line if other else None,
                "edge_type": e.edge_type or "calls",
                "confidence": round(e.confidence or 0.0, 3),
            }
            (callers if inbound else callees).append(entry)
        callers.sort(key=lambda x: (-x["confidence"], x["name"]))
        callees.sort(key=lambda x: (-x["confidence"], x["name"]))

    degrees = (
        await crud.get_node_degree_counts(session, repo_id, symbol_id)
        if node is not None
        else {"in_degree": 0, "out_degree": 0}
    )

    governing = [
        {"id": d.id, "title": d.title, "status": d.status}
        for d in await get_governing_decisions(session, repo_id, symbol_id)
    ]

    # Parent-file context for the "open file page" affordance.
    file_metrics = await crud.get_health_metrics(session, repo_id, file_paths=[sym.file_path])
    git_meta = await crud.get_git_metadata(session, repo_id, sym.file_path)
    file_context = {
        "file_path": sym.file_path,
        "health_score": round(file_metrics[0].score, 2) if file_metrics else None,
        "is_hotspot": bool(git_meta.is_hotspot) if git_meta else None,
        "primary_owner": git_meta.primary_owner_name if git_meta else None,
        "language": sym.language,
    }

    return {
        "symbol": symbol.model_dump(mode="json"),
        "graph": {
            "pagerank": round((node.pagerank if node else 0.0) or 0.0, 6),
            "in_degree": degrees["in_degree"],
            "out_degree": degrees["out_degree"],
            "callers": callers,
            "callees": callees,
        },
        "governing_decisions": governing,
        "file_context": file_context,
    }


@router.get("/by-name/{name}", response_model=list[SymbolResponse])
async def lookup_by_name(
    name: str,
    repo_id: str = Query(..., description="Repository ID"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[SymbolResponse]:
    """Look up symbols by exact or fuzzy name match.

    Returns exact matches first, then LIKE matches, up to 10 results.
    """

    result = await session.execute(
        select(WikiSymbol).where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.name == name,
        )
    )
    exact = list(result.scalars().all())
    if exact:
        return [SymbolResponse.from_orm(s) for s in exact]

    result = await session.execute(
        select(WikiSymbol)
        .where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.name.ilike(f"%{name}%"),
        )
        .limit(10)
    )
    fuzzy = result.scalars().all()
    return [SymbolResponse.from_orm(s) for s in fuzzy]


@router.get("/{symbol_db_id}", response_model=SymbolResponse)
async def get_symbol(
    symbol_db_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> SymbolResponse:
    """Get a single symbol by its database ID."""

    sym = await session.get(WikiSymbol, symbol_db_id)
    if sym is None:
        raise HTTPException(status_code=404, detail="Symbol not found")
    return SymbolResponse.from_orm(sym)
