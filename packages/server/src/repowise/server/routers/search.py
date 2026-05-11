"""/api/search — Semantic and full-text search.

In workspace mode the router fans out across every loaded repo's FTS
and vector store so a single query covers the whole workspace. Pass
``repo_id`` to scope a query to one repo.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from repowise.server.deps import get_fts, get_vector_store, verify_api_key
from repowise.server.schemas import SearchResultResponse

router = APIRouter(
    prefix="/api/search",
    tags=["search"],
    dependencies=[Depends(verify_api_key)],
)


def _to_response(r) -> SearchResultResponse:
    """Convert a SearchResult dataclass into the API schema."""
    return SearchResultResponse(
        page_id=r.page_id,
        title=r.title,
        page_type=r.page_type,
        target_path=r.target_path,
        score=r.score,
        snippet=r.snippet,
        search_type=r.search_type,
    )


@router.get("", response_model=list[SearchResultResponse])
async def search(
    request: Request,
    query: str = Query(..., min_length=1, description="Search query"),
    search_type: str = Query("semantic", description="semantic or fulltext"),
    limit: int = Query(10, ge=1, le=100),
    repo_id: str | None = Query(
        None,
        description=(
            "Scope to one workspace repo. Accepts a real repo_id from "
            "/api/repos, or the synthetic 'ws:<alias>' ID for an "
            "unindexed entry (returns empty)."
        ),
    ),
    fts=Depends(get_fts),  # noqa: B008
    vector_store=Depends(get_vector_store),  # noqa: B008
) -> list[SearchResultResponse]:
    """Search wiki pages by semantic similarity or full-text match.

    Behavior matrix:
      - Single-repo mode: uses the primary FTS / vector store as before.
      - Workspace mode + ``repo_id``: scopes to that repo's FTS / vector
        store; falls back to the primary if the repo isn't loaded yet.
      - Workspace mode without ``repo_id``: fans out across every loaded
        repo's index and merges results by score.
    """
    if search_type == "fulltext":
        results = await _fulltext(request, query, limit, repo_id=repo_id, primary_fts=fts)
    else:
        results = await _semantic(
            request, query, limit, repo_id=repo_id, primary_vs=vector_store
        )

    return [_to_response(r) for r in results]


# ---------------------------------------------------------------------------
# Fulltext fan-out
# ---------------------------------------------------------------------------


async def _fulltext(request: Request, query: str, limit: int, *, repo_id, primary_fts):
    """Run a fulltext search against the appropriate FTS instance(s)."""
    ws_fts: dict = getattr(request.app.state, "workspace_fts", {}) or {}

    # Single-repo mode (no workspace_fts registry) → use the primary FTS.
    if not ws_fts:
        return await primary_fts.search(query, limit=limit)

    # Workspace mode with explicit repo_id.
    if repo_id is not None:
        # Synthetic IDs ("ws:<alias>") point at unindexed entries — no
        # FTS data, so return an empty list rather than fall back to the
        # primary and confuse the user.
        if repo_id.startswith("ws:"):
            return []
        target_fts = ws_fts.get(repo_id)
        if target_fts is None:
            # Unknown repo_id — fall back to primary so callers don't
            # silently get nothing.
            return await primary_fts.search(query, limit=limit)
        return await target_fts.search(query, limit=limit)

    # Workspace mode, no filter → fan out across every loaded FTS,
    # merge by score, and cap at limit.
    all_results = []
    for fts_inst in ws_fts.values():
        try:
            per_repo = await fts_inst.search(query, limit=limit)
        except Exception:
            continue
        all_results.extend(per_repo)
    all_results.sort(key=lambda r: r.score, reverse=True)
    return all_results[:limit]


# ---------------------------------------------------------------------------
# Semantic fan-out
# ---------------------------------------------------------------------------


async def _semantic(request: Request, query: str, limit: int, *, repo_id, primary_vs):
    """Run a semantic search across the appropriate vector store(s)."""
    from repowise.server.search_helpers import resolve_workspace_vector_store

    ws_config = getattr(request.app.state, "workspace_config", None)
    if ws_config is None:
        # Single-repo mode.
        return await primary_vs.search(query, limit=limit)

    if repo_id is not None:
        if repo_id.startswith("ws:"):
            return []
        vs = await resolve_workspace_vector_store(request.app, repo_id)
        if vs is None:
            return await primary_vs.search(query, limit=limit)
        return await vs.search(query, limit=limit)

    # Fan-out: iterate over every workspace repo with an indexed wiki.db
    # and try to load its persisted LanceDB store. Repos without one
    # fall back to FTS so the user still gets some signal.
    from pathlib import Path as _P

    ws_root = getattr(request.app.state, "workspace_root", None)
    if ws_root is None:
        return await primary_vs.search(query, limit=limit)
    ws_root_path = _P(ws_root)

    all_results = []
    workspace_fts = getattr(request.app.state, "workspace_fts", {}) or {}

    for entry in ws_config.repos:
        repo_path = (ws_root_path / entry.path).resolve()
        db_path = repo_path / ".repowise" / "wiki.db"
        if not db_path.exists():
            continue
        # Resolve repo_id from the per-repo DB once (cached on app.state).
        import sqlite3 as _sql

        try:
            with _sql.connect(str(db_path)) as conn:
                row = conn.execute(
                    "SELECT id FROM repositories LIMIT 1"
                ).fetchone()
        except Exception:
            row = None
        rid = row[0] if row else None

        # Try the vector store first.
        per_repo = []
        vs = None
        if rid is not None:
            try:
                vs = await resolve_workspace_vector_store(request.app, rid)
            except Exception:
                vs = None
        if vs is not None:
            try:
                per_repo = await vs.search(query, limit=limit)
            except Exception:
                per_repo = []
        # Fall back to FTS when no vector data is available — better
        # than silently returning nothing on workspaces that haven't
        # built LanceDB indexes yet.
        if not per_repo and rid in workspace_fts:
            try:
                per_repo = await workspace_fts[rid].search(query, limit=limit)
            except Exception:
                per_repo = []
        all_results.extend(per_repo)

    all_results.sort(key=lambda r: r.score, reverse=True)
    return all_results[:limit]
