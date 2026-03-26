"""MCP Tool 5: search_codebase — semantic search over the wiki."""

from __future__ import annotations

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    GitMetadata,
    Page,
)
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import _get_repo
from repowise.server.mcp_server._server import mcp


@mcp.tool()
async def search_codebase(
    query: str,
    limit: int = 5,
    page_type: str | None = None,
    repo: str | None = None,
) -> dict:
    """Semantic search over the full wiki. Ask in natural language.

    Args:
        query: Natural language search query (e.g. "how does authentication work?").
        limit: Maximum results to return (default 5).
        page_type: Optional filter by page type (file_page, module_page, etc.).
        repo: Repository path, name, or ID.
    """
    async with get_session(_state._session_factory) as session:
        # Ensure repo exists
        await _get_repo(session, repo)

    # Try semantic search first, fall back to fulltext
    # Over-fetch when filtering by page_type to avoid returning 0 results
    fetch_limit = limit * 3 if page_type else limit
    results = []
    try:
        results = await _state._vector_store.search(query, limit=fetch_limit)
    except Exception:
        pass
    if not results:
        try:
            results = await _state._fts.search(query, limit=fetch_limit)
        except Exception:
            pass

    output = []
    for r in results:
        if page_type and r.page_type != page_type:
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
        async with get_session(_state._session_factory) as session:
            res = await session.execute(
                select(Page.id, Page.target_path).where(
                    Page.id.in_(page_ids)
                )
            )
            page_info = {row[0]: row[1] for row in res.all()}

            # Build git freshness map for result file paths
            target_paths = [
                tp for tp in page_info.values() if tp
            ]
            git_map: dict[str, GitMetadata] = {}
            if target_paths:
                git_res = await session.execute(
                    select(GitMetadata).where(
                        GitMetadata.file_path.in_(target_paths)
                    )
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
                item["relevance_score"] = round(
                    item["relevance_score"] * (1 + 0.2 * recency), 4
                )

        # Re-sort by boosted relevance
        output.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

    # Derive confidence_score from relative position in the result set.
    # The top result gets 1.0; others are scaled proportionally by their
    # relevance score relative to the best match.
    if output:
        max_score = max(
            (item.get("relevance_score") or 0) for item in output
        )
        for item in output:
            raw = item.get("relevance_score") or 0
            item["confidence_score"] = round(raw / max_score, 2) if max_score > 0 else 0.0

    return {"results": output}
