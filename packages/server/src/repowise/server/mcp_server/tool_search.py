"""MCP Tool 5: search_codebase — semantic search over the wiki."""

from __future__ import annotations

import asyncio
import contextlib
import re

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
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.core.registry import mcp_tool_registry as mcp

# Minimum relevance score below which results are dropped. Prevents
# returning semantically unrelated pages when the corpus has no real match.
_MIN_RELEVANCE_SCORE = 0.03

# Pure-identifier pattern: a single bareword that looks like a code symbol
# (no spaces, no punctuation other than _/.). These are almost always
# better handled by Grep than by semantic search — vector embeddings of a
# bare ``getCurrentUser`` quickly drift to thematically-similar but
# textually-distant pages, while Grep would have found the literal usage
# in milliseconds. We hint to Grep but still run the search so callers
# that genuinely want fuzzy symbol search are not blocked.
_IDENT_QUERY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{1,29}$")


def _looks_like_exact_token(query: str) -> bool:
    """True when the query is a single identifier-shaped token best served by Grep."""
    stripped = query.strip()
    if not stripped or " " in stripped:
        return False
    return bool(_IDENT_QUERY_RE.match(stripped))


# Path-prefix heuristics for the ``kind`` filter. We classify a hit's
# target_path against these prefixes; if none match, the hit falls into
# ``other`` and is dropped only when the caller asked for a specific kind.
_TEST_PATH_TOKENS = ("/test/", "/tests/", "/__tests__/", "test_", "_test.", ".spec.", ".test.")
_CONFIG_PATH_TOKENS = (
    "pyproject.toml", "package.json", "tsconfig", "setup.py", "setup.cfg",
    "/.github/", "dockerfile", ".yml", ".yaml", ".toml", ".ini", ".cfg",
    "lockfile", "package-lock", "uv.lock", "poetry.lock",
)


def _classify_hit_kind(target_path: str, page_type: str) -> str:
    """Bucket a hit into implementation / test / config / doc."""
    tp = (target_path or "").lower()
    if page_type == "module_page" or page_type == "symbol_spotlight" or tp.endswith(".md"):
        return "doc"
    if any(tok in tp for tok in _TEST_PATH_TOKENS):
        return "test"
    if any(tok in tp for tok in _CONFIG_PATH_TOKENS):
        return "config"
    return "implementation"


async def _search_single_repo(
    ctx, query: str, limit: int, page_type: str | None
) -> tuple[list[dict], str]:
    """Run search against a single repo context.

    Returns ``(results, method)`` where ``method`` is ``"embedding"`` or
    ``"bm25"`` so the caller can surface which retrieval backend produced
    the list — embedding misses fall through to FTS silently in the existing
    code, and the agent has no way to distinguish a strong embedding hit
    from a fallback BM25 hit otherwise.
    """
    # Wait for vector store readiness
    if ctx.vector_store_ready is not None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ctx.vector_store_ready.wait(), timeout=30.0)

    fetch_limit = limit * 3 if page_type else limit
    results = []
    method = "embedding"
    with contextlib.suppress(TimeoutError, Exception):
        results = await asyncio.wait_for(
            ctx.vector_store.search(query, limit=fetch_limit),
            timeout=8.0,
        )
    if not results:
        method = "bm25"
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

    return output[:limit], method


async def _federated_search(query: str, limit: int, page_type: str | None) -> dict:
    """Search across all repos using Reciprocal Rank Fusion."""
    contexts = await _resolve_all_contexts()
    all_results = []

    for ctx in contexts:
        repo_results, repo_method = await _search_single_repo(ctx, query, limit, page_type)
        for rank, item in enumerate(repo_results):
            item["repo"] = ctx.alias
            item["rrf_score"] = 1.0 / (rank + 60)  # RRF constant k=60
            item["search_method"] = repo_method
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

    return {"results": output, "_meta": _build_meta()}


@mcp.tool()
async def search_codebase(
    query: str,
    limit: int = 5,
    page_type: str | None = None,
    kind: str | None = None,
    repo: str | None = None,
) -> dict:
    """Find pages by concept — semantic search across the wiki.

    The right tool when ``get_answer`` punted and you need candidate files for
    a conceptual query ("authentication flow", "rate limiting", "where do we
    handle webhooks"). For exact identifiers or token matches, use Grep — it
    is faster and never drifts thematically. This tool will surface a Grep
    hint when the query looks like a bare identifier.

    Each result carries ``search_method`` (``"embedding"`` or ``"bm25"``) so
    the caller can tell whether semantic retrieval succeeded or fell back to
    keyword scoring; BM25 hits warrant more verification than embedding hits.

    Args:
        query: natural-language search query.
        limit: maximum number of results to return (default 5).
        page_type: filter on page kind (``file_page``, ``module_page``,
            ``symbol_spotlight``).
        kind: filter by file role: ``"implementation"`` | ``"test"`` |
            ``"config"`` | ``"doc"``. Path-prefix heuristic; trims the result
            list rather than rewriting the query.
        repo: repository alias, or ``"all"`` for workspace-wide search.
    """
    grep_hint: str | None = None
    if _looks_like_exact_token(query):
        grep_hint = (
            f"Query {query!r} looks like an exact identifier. Grep will find "
            "literal usages faster and without thematic drift. This tool ran "
            "the search anyway — verify before relying on the results."
        )

    if repo == "all":
        federated = await _federated_search(query, limit, page_type)
        if kind:
            federated["results"] = [
                r for r in federated["results"]
                if _classify_hit_kind(r.get("target_path", ""), r.get("page_type", "")) == kind
            ]
        if grep_hint:
            federated["grep_hint"] = grep_hint
        return federated

    ctx = await _resolve_repo_context(repo)

    async with get_session(ctx.session_factory) as session:
        # Validate repo exists in DB
        repository = await _get_repo(session)

    # Wait for vector store readiness
    if ctx.vector_store_ready is not None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ctx.vector_store_ready.wait(), timeout=30.0)

    # Try semantic search, fall back to FTS. Track which backend supplied the
    # hits so the response can surface it per-result — silent fallback hides
    # a quality cliff that the agent should weigh into its trust budget.
    fetch_limit = limit * 3 if page_type else limit
    results = []
    search_method = "embedding"
    with contextlib.suppress(TimeoutError, Exception):
        results = await asyncio.wait_for(
            ctx.vector_store.search(query, limit=fetch_limit),
            timeout=8.0,
        )
    if not results:
        search_method = "bm25"
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
                "search_method": search_method,
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
        # Attach target_path to each item so the kind filter (path-prefix
        # heuristic) and downstream get_context callers can act on it.
        for item in output:
            item["target_path"] = page_info.get(item["page_id"], "")

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

    if kind:
        output = [
            item for item in output
            if _classify_hit_kind(item.get("target_path", ""), item.get("page_type", "")) == kind
        ]

    response: dict = {"results": output, "_meta": _build_meta(repository=repository)}
    if grep_hint:
        response["grep_hint"] = grep_hint
    return response
