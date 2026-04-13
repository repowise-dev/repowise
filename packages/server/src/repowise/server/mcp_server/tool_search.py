"""MCP Tool 5: search_codebase — semantic search over the wiki."""

from __future__ import annotations

import asyncio
import contextlib

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    GitMetadata,
    Page,
)
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _resolve_all_contexts,
    _resolve_repo_context,
)
from repowise.server.mcp_server._server import mcp

# Minimum relevance score below which results are dropped. Prevents
# returning semantically unrelated pages when the corpus has no real match.
_MIN_RELEVANCE_SCORE = 0.03


async def _search_single_repo(ctx, query: str, limit: int, page_type: str | None):
    """Run search against a single repo context. Returns list of result dicts."""
    # Wait for vector store readiness
    if ctx.vector_store_ready is not None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ctx.vector_store_ready.wait(), timeout=30.0)

    fetch_limit = limit * 3 if page_type else limit
    results = []
    with contextlib.suppress(TimeoutError, Exception):
        results = await asyncio.wait_for(
            ctx.vector_store.search(query, limit=fetch_limit),
            timeout=8.0,
        )
    if not results:
        with contextlib.suppress(Exception):
            results = await ctx.fts.search(query, limit=fetch_limit)

    output = []
    for r in results:
        if page_type and r.page_type != page_type:
            continue
        if r.score < _MIN_RELEVANCE_SCORE:
            continue
        output.append({
            "page_id": r.page_id,
            "title": r.title,
            "page_type": r.page_type,
            "snippet": r.snippet,
            "relevance_score": r.score,
        })

    return output[:limit]


async def _federated_search(query: str, limit: int, page_type: str | None) -> dict:
    """Search across all repos using Reciprocal Rank Fusion."""
    contexts = await _resolve_all_contexts()
    all_results = []

    for ctx in contexts:
        repo_results = await _search_single_repo(ctx, query, limit, page_type)
        for rank, item in enumerate(repo_results):
            item["repo"] = ctx.alias
            item["rrf_score"] = 1.0 / (rank + 60)  # RRF constant k=60
        all_results.extend(repo_results)

    # Sort by RRF score and take top N
    all_results.sort(key=lambda x: x.get("rrf_score", 0), reverse=True)
    output = all_results[:limit]

    # Derive confidence from RRF position
    if output:
        max_rrf = max(item.get("rrf_score", 0) for item in output)
        for item in output:
            raw = item.get("rrf_score", 0)
            item["confidence_score"] = round(raw / max_rrf, 2) if max_rrf > 0 else 0.0

    return {"results": output}


@mcp.tool()
async def search_codebase(
    query: str,
    limit: int = 5,
    page_type: str | None = None,
    repo: str | None = None,
) -> dict:
    """Semantic search over the wiki. Use when ``get_answer`` did not return
    a confident result and you need to discover candidate pages by topic.

    In workspace mode, use ``repo="all"`` to search across all repos.

    Args:
        query: natural-language search query.
        limit: maximum number of results to return (default 5).
        page_type: optional filter on page kind (e.g. ``file_page``,
            ``module_page``, ``symbol_spotlight``).
        repo: repository alias, or ``"all"`` for workspace-wide search.
    """
    if repo == "all":
        return await _federated_search(query, limit, page_type)

    ctx = await _resolve_repo_context(repo)

    async with get_session(ctx.session_factory) as session:
        # Validate repo exists in DB
        await _get_repo(session)

    # Wait for vector store readiness
    if ctx.vector_store_ready is not None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ctx.vector_store_ready.wait(), timeout=30.0)

    # Try semantic search, fall back to FTS.
    fetch_limit = limit * 3 if page_type else limit
    results = []
    with contextlib.suppress(TimeoutError, Exception):
        results = await asyncio.wait_for(
            ctx.vector_store.search(query, limit=fetch_limit),
            timeout=8.0,
        )
    if not results:
        with contextlib.suppress(Exception):
            results = await ctx.fts.search(query, limit=fetch_limit)

    output = []
    for r in results:
        if page_type and r.page_type != page_type:
            continue
        if r.score < _MIN_RELEVANCE_SCORE:
            continue
        output.append(
            {
                "page_id": r.page_id,
                "title": r.title,
                "page_type": r.page_type,
                "snippet": r.snippet,
                "relevance_score": r.score,
            }
        )

    output = output[:limit]

    # Batch-lookup page target paths for git freshness boost
    if output:
        page_ids = [item["page_id"] for item in output]
        async with get_session(ctx.session_factory) as session:
            res = await session.execute(
                select(Page.id, Page.target_path).where(Page.id.in_(page_ids))
            )
            page_info = {row[0]: row[1] for row in res.all()}

            # Build git freshness map for result file paths
            target_paths = [tp for tp in page_info.values() if tp]
            git_map: dict[str, GitMetadata] = {}
            if target_paths:
                git_res = await session.execute(
                    select(GitMetadata).where(GitMetadata.file_path.in_(target_paths))
                )
                git_map = {g.file_path: g for g in git_res.scalars().all()}

        for item in output:
            # Freshness boost: recently-active files rank higher
            target_path = page_info.get(item["page_id"])
            gm = git_map.get(target_path) if target_path else None
            if gm and item.get("relevance_score"):
                c30 = gm.commit_count_30d or 0
                c90 = gm.commit_count_90d or 0
                if c30 > 0:
                    recency = 1.0
                elif c90 > 0:
                    recency = 0.5
                else:
                    recency = 0.0
                item["relevance_score"] = round(item["relevance_score"] * (1 + 0.2 * recency), 4)

        # Re-sort by boosted relevance
        output.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

    # Derive confidence_score from relative position in the result set.
    if output:
        max_score = max((item.get("relevance_score") or 0) for item in output)
        for item in output:
            raw = item.get("relevance_score") or 0
            item["confidence_score"] = round(raw / max_score, 2) if max_score > 0 else 0.0

    return {"results": output}
