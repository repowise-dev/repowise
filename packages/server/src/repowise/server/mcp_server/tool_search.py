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
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _is_path,
    _resolve_all_contexts,
    _resolve_repo_context,
    filter_dicts_by_key,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server.tool_search_symbols import (
    search_paths_single,
    search_symbols_single,
)

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


# Identifier-shaped tokens inside a longer query: snake_case of any casing
# (≥1 underscore, incl. _UPPER_SNAKE constants) or CamelCase (≥2 humps).
# Plain English words never match.
_IDENT_TOKEN_RE = re.compile(
    r"\b(?:_*[A-Za-z0-9]+_[A-Za-z0-9_]+|[A-Z][a-z][a-z0-9]*(?:[A-Z][a-z0-9]+)+)\b"
)


def _embedded_identifiers(query: str) -> list[str]:
    """Identifier-shaped tokens carried inside a natural-language query."""
    return _IDENT_TOKEN_RE.findall(query)


# Decision records are short, dense title-statements; they win cosine
# similarity against long file-page embeddings on any query containing
# design nouns ("store", "SQLite", "cap", "prune") and crowd file pages
# out of the top ranks entirely. Down-weight them unless the query is
# why-shaped — rationale questions are get_why's territory, but a caller
# who phrases one here clearly wants the decision pages ranked honestly.
_DECISION_DOWNWEIGHT = 0.6

_WHY_SHAPED_RE = re.compile(
    r"^\s*(why|when\s+did|when\s+was|who\s+decided|who\s+chose|what\s+was\s+the\s+(reason|rationale))\b"
    r"|\b(decision|decided|rationale|adr)\b",
    re.IGNORECASE,
)


def _is_why_shaped(query: str) -> bool:
    """True when the query asks for rationale, so decision records should rank naturally."""
    return bool(_WHY_SHAPED_RE.search(query))


def _downweight_decisions(output: list[dict], query: str) -> None:
    """Scale decision_record relevance in place unless the query is why-shaped."""
    if _is_why_shaped(query):
        return
    for item in output:
        if item.get("page_type") == "decision_record" and item.get("relevance_score"):
            item["relevance_score"] = round(item["relevance_score"] * _DECISION_DOWNWEIGHT, 4)


def _sort_demoting_decisions(output: list[dict], query: str) -> None:
    """Sort by relevance, ranking decision records below every file-backed
    page on non-why queries.

    The multiplicative down-weight alone is washed out when decision scores
    dominate (short dense titles win cosine similarity by a margin > the
    0.6 factor) — observed live as 5/5 irrelevant decisions for a plain
    implementation query. For non-why queries a decision record is never a
    better first Read than a file page, so the demotion is absolute; the
    down-weighted score still orders decisions among themselves.
    """
    why = _is_why_shaped(query)

    def key(item: dict) -> tuple:
        demoted = (not why) and item.get("page_type") == "decision_record"
        return (1 if demoted else 0, -(item.get("relevance_score") or 0.0))

    output.sort(key=key)


async def _non_decision_fallback(ctx, query: str, fetch_limit: int) -> list[dict]:
    """Wider re-fetch keeping only non-decision pages.

    Guard for the window-saturation failure: when the entire over-fetched
    window is decision records, demotion has nothing to promote and a
    non-why query returns zero implementation pages. Fetch 4x wider, drop
    decisions, and let the caller merge the survivors.
    """
    results = []
    with contextlib.suppress(TimeoutError, Exception):
        results = await asyncio.wait_for(
            ctx.vector_store.search(query, limit=fetch_limit * 4),
            timeout=8.0,
        )
    if not results:
        with contextlib.suppress(Exception):
            results = await ctx.fts.search(query, limit=fetch_limit * 4)

    out = []
    for r in results:
        if r.page_type == "decision_record":
            continue
        if r.score < _MIN_RELEVANCE_SCORE:
            continue
        out.append(
            {
                "page_id": r.page_id,
                "title": r.title,
                "page_type": r.page_type,
                "snippet": r.snippet,
                "relevance_score": r.score,
            }
        )
    return out


async def _rescue_all_decision_window(
    ctx, output: list[dict], query: str, fetch_limit: int
) -> list[dict]:
    """Merge non-decision pages into an all-decision result window.

    No-op unless the query is non-why AND every current hit is a decision
    record. Deduplicates by page_id.
    """
    if not output or _is_why_shaped(query):
        return output
    if not all(item.get("page_type") == "decision_record" for item in output):
        return output
    fallback = await _non_decision_fallback(ctx, query, fetch_limit)
    seen = {item["page_id"] for item in output}
    output.extend(item for item in fallback if item["page_id"] not in seen)
    return output


def _fetch_limit_for(limit: int, kind: str | None) -> int:
    """Over-fetch headroom for post-filters and decision down-weighting.

    Always over-fetch at least 3x: without headroom the down-weighting can
    only reorder a window that decision records may already fill, so file
    pages never surface. ``kind`` trims hardest (decision/module/overview
    pages all classify as "doc"), so it gets 6x — 3x was measured to leave
    zero implementation pages in the window on decision-heavy queries.
    """
    return limit * (6 if kind else 3)


# Path-prefix heuristics for the ``kind`` filter. We classify a hit's
# target_path against these prefixes; if none match, the hit falls into
# ``other`` and is dropped only when the caller asked for a specific kind.
_TEST_PATH_TOKENS = ("/test/", "/tests/", "/__tests__/", "test_", "_test.", ".spec.", ".test.")
_CONFIG_PATH_TOKENS = (
    "pyproject.toml",
    "package.json",
    "tsconfig",
    "setup.py",
    "setup.cfg",
    "/.github/",
    "dockerfile",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    "lockfile",
    "package-lock",
    "uv.lock",
    "poetry.lock",
)


def _classify_hit_kind(target_path: str, page_type: str) -> str:
    """Bucket a hit into implementation / test / config / doc.

    Pages without a file behind them (decision records, repo overviews,
    onboarding pages — all have empty ``target_path``) are docs: letting
    them fall through to the path heuristics classified every decision
    record as "implementation", so ``kind="implementation"`` returned
    decision pages instead of filtering them out.
    """
    tp = (target_path or "").lower()
    if page_type in ("module_page", "symbol_spotlight") or tp.endswith(".md"):
        return "doc"
    if not tp or page_type not in ("file_page",):
        return "doc"
    if any(tok in tp for tok in _TEST_PATH_TOKENS):
        return "test"
    if any(tok in tp for tok in _CONFIG_PATH_TOKENS):
        return "config"
    return "implementation"


def _filter_by_kind(output: list[dict], kind: str | None) -> list[dict]:
    """Keep only hits classified as ``kind`` (no-op when ``kind`` is falsy).

    Runs on the over-fetched list BEFORE the limit cut so the caller still
    gets up to ``limit`` results of the requested kind.
    """
    if not kind:
        return output
    return [
        item
        for item in output
        if _classify_hit_kind(item.get("target_path", ""), item.get("page_type", "")) == kind
    ]


async def _load_page_info(
    session, output: list[dict], *, with_git: bool = False
) -> tuple[dict, set, dict]:
    """Batch-load target paths, tombstones, and optionally git metadata.

    Returns ``(page_info, tombstoned, git_map)`` where ``page_info`` maps
    page_id -> target_path, ``tombstoned`` is the set of tombstoned page_ids,
    and ``git_map`` maps file_path -> GitMetadata (empty unless ``with_git``).
    """
    page_ids = [item["page_id"] for item in output]
    res = await session.execute(
        select(Page.id, Page.target_path, Page.freshness_status).where(Page.id.in_(page_ids))
    )
    rows = res.all()
    page_info = {row[0]: row[1] for row in rows}
    tombstoned = {row[0] for row in rows if row[2] == "tombstone"}

    git_map: dict[str, GitMetadata] = {}
    if with_git:
        target_paths = [tp for tp in page_info.values() if tp]
        if target_paths:
            git_res = await session.execute(
                select(GitMetadata).where(GitMetadata.file_path.in_(target_paths))
            )
            git_map = {g.file_path: g for g in git_res.scalars().all()}
    return page_info, tombstoned, git_map


def _apply_freshness_boost(item: dict, gm: GitMetadata | None) -> None:
    """Boost an item's relevance for recently-active files (in place)."""
    if not gm or not item.get("relevance_score"):
        return
    c30 = gm.commit_count_30d or 0
    c90 = gm.commit_count_90d or 0
    if c30 > 0:
        recency = 1.0
    elif c90 > 0:
        recency = 0.5
    else:
        recency = 0.0
    item["relevance_score"] = round(item["relevance_score"] * (1 + 0.2 * recency), 4)


def _assign_confidence(output: list[dict], score_key: str, target_key: str) -> None:
    """Derive ``target_key`` for each item from its ``score_key`` position (in place)."""
    if not output:
        return
    max_score = max((item.get(score_key) or 0) for item in output)
    for item in output:
        raw = item.get(score_key) or 0
        item[target_key] = round(raw / max_score, 2) if max_score > 0 else 0.0


async def _wait_for_vector_store(ctx) -> None:
    """Block (bounded) until the vector store signals readiness, if it tracks it."""
    if ctx.vector_store_ready is not None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ctx.vector_store_ready.wait(), timeout=30.0)


async def _retrieve_with_method(ctx, query: str, fetch_limit: int) -> tuple[list, str]:
    """Try semantic search, fall back to FTS.

    Returns ``(results, method)`` where ``method`` is ``"embedding"`` or
    ``"bm25"`` so callers can surface which retrieval backend produced the
    list — embedding misses fall through to FTS silently otherwise.
    """
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
    return results, method


def _build_output(
    results: list, page_type: str | None, search_method: str | None = None
) -> list[dict]:
    """Map raw search hits to result dicts, dropping off-type and low-score hits.

    When ``search_method`` is given it is attached to each item; the federated
    path attaches it later per-repo so it passes ``None``.
    """
    output = []
    for r in results:
        if page_type and r.page_type != page_type:
            continue
        if r.score < _MIN_RELEVANCE_SCORE:
            continue
        item = {
            "page_id": r.page_id,
            "title": r.title,
            "page_type": r.page_type,
            "snippet": r.snippet,
            "relevance_score": r.score,
        }
        if search_method is not None:
            item["search_method"] = search_method
        output.append(item)
    return output


async def _search_single_repo(
    ctx, query: str, limit: int, page_type: str | None, kind: str | None = None
) -> tuple[list[dict], str]:
    """Run search against a single repo context.

    Returns ``(results, method)`` where ``method`` is ``"embedding"`` or
    ``"bm25"`` so the caller can surface which retrieval backend produced
    the list — embedding misses fall through to FTS silently in the existing
    code, and the agent has no way to distinguish a strong embedding hit
    from a fallback BM25 hit otherwise.

    The ``kind`` filter runs here, on the over-fetched list and BEFORE the
    limit cut — filtering after per-repo truncation returned fewer than
    ``limit`` results (frequently zero) in the federated path.
    """
    await _wait_for_vector_store(ctx)

    fetch_limit = _fetch_limit_for(limit, kind)
    results, method = await _retrieve_with_method(ctx, query, fetch_limit)
    output = _build_output(results, page_type)

    _downweight_decisions(output, query)
    output = await _rescue_all_decision_window(ctx, output, query, fetch_limit)
    _sort_demoting_decisions(output, query)

    # Attach target_path and drop excluded hits per repo (so federated search
    # honours each repo's own exclude_patterns) before aggregation. Runs on
    # the over-fetched list so the kind filter below has real headroom.
    if output:
        async with get_session(ctx.session_factory) as session:
            page_info, tombstoned, _ = await _load_page_info(session, output)
        output = [item for item in output if item["page_id"] not in tombstoned]
        for item in output:
            item["target_path"] = page_info.get(item["page_id"], "")
        output = filter_dicts_by_key(output, "target_path", _get_exclude_spec(ctx.path))

    output = _filter_by_kind(output, kind)
    return output[:limit], method


async def _federated_search(
    query: str, limit: int, page_type: str | None, kind: str | None = None
) -> dict:
    """Search across all repos using Reciprocal Rank Fusion."""
    contexts = await _resolve_all_contexts()
    all_results = []

    for ctx in contexts:
        repo_results, repo_method = await _search_single_repo(ctx, query, limit, page_type, kind)
        for rank, item in enumerate(repo_results):
            item["repo"] = ctx.alias
            item["rrf_score"] = 1.0 / (rank + 60)  # RRF constant k=60
            item["search_method"] = repo_method
        all_results.extend(repo_results)

    # Sort by RRF score and take top N
    all_results.sort(key=lambda x: x.get("rrf_score", 0), reverse=True)
    output = all_results[:limit]

    # Derive confidence from RRF position
    _assign_confidence(output, "rrf_score", "confidence_score")

    return {"results": output, "_meta": _build_meta()}


def _grep_hint_for(query: str) -> str | None:
    """Build a Grep nudge for identifier-shaped queries, else ``None``."""
    if _looks_like_exact_token(query):
        return (
            f"Query {query!r} looks like an exact identifier. Grep will find "
            "literal usages faster and without thematic drift. This tool ran "
            "the search anyway — verify before relying on the results."
        )
    if idents := _embedded_identifiers(query):
        shown = ", ".join(repr(t) for t in idents[:3])
        return (
            f"Query names identifier(s) {shown} — for their exact "
            "definition/usages, Grep is faster and never drifts "
            "thematically. Semantic results below are concept-level."
        )
    return None


_VALID_MODES = {"auto", "concept", "symbol", "path", "hybrid"}


def _resolve_mode(query: str, mode: str | None) -> str:
    """Resolve ``mode="auto"`` to a concrete branch from the query shape.

    Explicit modes pass through. ``auto`` routes path-shaped queries to path
    search, single identifier-shaped tokens to symbol search, and queries that
    merely *carry* an identifier inside natural language to hybrid; everything
    else stays concept (the original wiki-semantic path). The routing reuses
    the exact heuristics that previously only emitted a grep_hint.
    """
    m = (mode or "auto").lower()
    if m not in _VALID_MODES:
        m = "auto"
    if m != "auto":
        return m
    if _is_path(query):
        return "path"
    if _looks_like_exact_token(query):
        return "symbol"
    if _embedded_identifiers(query):
        return "hybrid"
    return "concept"


async def _contexts_for(repo: str | None) -> list:
    """The repo contexts a structured search runs over (one, or all in 'all')."""
    if repo == "all":
        return await _resolve_all_contexts()
    return [await _resolve_repo_context(repo)]


def _tag_repo(items: list[dict], ctx, multi: bool) -> None:
    if multi:
        for item in items:
            item["repo"] = ctx.alias


async def _structured_search(
    query: str,
    limit: int,
    page_type: str | None,
    kind: str | None,
    symbol_kind: str | None,
    repo: str | None,
    mode: str,
    grep_hint: str | None,
) -> dict:
    """Run symbol / path / hybrid search and shape the response.

    Honours ``repo="all"`` (federates across contexts, then re-ranks by score),
    per-repo ``exclude_patterns`` and tombstones (enforced inside the
    single-repo helpers). The grep_hint is attached only as a fallback — when
    the structural index produced nothing, the agent still has a path forward.
    """
    contexts = await _contexts_for(repo)
    multi = len(contexts) > 1

    symbols: list[dict] = []
    files: list[dict] = []
    concepts: list[dict] = []

    for ctx in contexts:
        if mode in ("symbol", "hybrid"):
            s = await search_symbols_single(ctx, query, limit, symbol_kind=symbol_kind, kind=kind)
            _tag_repo(s, ctx, multi)
            symbols.extend(s)
        if mode == "path":
            f = await search_paths_single(ctx, query, limit)
            _tag_repo(f, ctx, multi)
            files.extend(f)
        if mode == "hybrid":
            c, method = await _search_single_repo(ctx, query, limit, page_type, kind)
            for item in c:
                item.setdefault("search_method", method)
                item["type"] = "page"
            _tag_repo(c, ctx, multi)
            concepts.extend(c)

    symbols.sort(key=lambda x: -(x.get("score") or 0.0))
    files.sort(key=lambda x: -(x.get("score") or 0.0))

    if mode == "symbol":
        results = symbols[:limit]
    elif mode == "path":
        results = files[:limit]
    else:  # hybrid — symbol matches first, then concept pages for new files
        sym_files = {s.get("file") for s in symbols}
        concepts = [c for c in concepts if c.get("target_path") not in sym_files]
        # Federation appends per-repo concept lists in repo order — re-rank by
        # relevance so a strong page in repo B isn't buried under repo A's weak
        # ones. (Single-repo: already sorted upstream; this is a no-op.)
        concepts.sort(key=lambda x: -(x.get("relevance_score") or 0.0))
        # Reserve up to half the window for concept pages so hybrid stays
        # hybrid: a flood of symbol matches must not truncate every page out.
        reserved = min(len(concepts), limit // 2)
        results = (symbols[: max(1, limit - reserved)] + concepts)[:limit]

    repository = None
    if not multi:
        async with get_session(contexts[0].session_factory) as session:
            repository = await _get_repo(session)

    response: dict = {"results": results, "mode": mode, "_meta": _build_meta(repository=repository)}
    if grep_hint and not results:
        response["grep_hint"] = grep_hint
    return response


@mcp.tool()
async def search_codebase(
    query: str,
    limit: int = 5,
    page_type: str | None = None,
    kind: str | None = None,
    repo: str | None = None,
    mode: str = "auto",
    symbol_kind: str | None = None,
) -> dict:
    """Find code by concept, symbol, or path — hybrid codebase search.

    mode="auto" (default) routes the query: identifier-shaped queries search
    the indexed symbols (returns symbol_id/file/line bounds — pipe into
    get_symbol), path-shaped queries resolve files (pipe into get_context),
    and conceptual queries ("rate limiting", "where do we handle webhooks")
    run wiki-semantic search. Mixed natural-language + identifier queries run
    hybrid (symbol hits first, then concept pages). Concept results carry
    search_method ("embedding" or "bm25" fallback: verify those); decision
    records rank below file pages unless the query is why-shaped.

    Args:
        query: identifier, path, or natural-language query.
        limit: max results (default 5).
        page_type: file_page | module_page | symbol_spotlight (concept only).
        kind: implementation | test | config | doc (concept/symbol modes).
        repo: alias, or "all" for workspace-wide.
        mode: auto | concept | symbol | path | hybrid.
        symbol_kind: filter symbol hits by kind (function|class|method|...).
    """
    grep_hint = _grep_hint_for(query)
    resolved_mode = _resolve_mode(query, mode)

    if resolved_mode in ("symbol", "path", "hybrid"):
        return await _structured_search(
            query, limit, page_type, kind, symbol_kind, repo, resolved_mode, grep_hint
        )

    if repo == "all":
        # kind is filtered per-repo inside _search_single_repo, before each
        # repo's limit cut, so the fused list is already kind-pure and full.
        federated = await _federated_search(query, limit, page_type, kind)
        if grep_hint:
            federated["grep_hint"] = grep_hint
        return federated

    ctx = await _resolve_repo_context(repo)

    async with get_session(ctx.session_factory) as session:
        # Validate repo exists in DB
        repository = await _get_repo(session)

    await _wait_for_vector_store(ctx)

    # Try semantic search, fall back to FTS. Track which backend supplied the
    # hits so the response can surface it per-result — silent fallback hides
    # a quality cliff that the agent should weigh into its trust budget.
    # Always over-fetch (see _fetch_limit_for): post-filters trim hits, and
    # decision down-weighting needs file pages inside the window to promote.
    fetch_limit = _fetch_limit_for(limit, kind)
    results, search_method = await _retrieve_with_method(ctx, query, fetch_limit)
    output = _build_output(results, page_type, search_method)

    _downweight_decisions(output, query)
    rescued = await _rescue_all_decision_window(ctx, output, query, fetch_limit)
    if len(rescued) > len(output):
        # Fallback entries enter after the method was determined; they came
        # from the same backend.
        for item in rescued:
            item.setdefault("search_method", search_method)
        output = rescued

    # Batch-lookup page target paths for the kind filter + git freshness boost
    if output:
        async with get_session(ctx.session_factory) as session:
            page_info, tombstoned, git_map = await _load_page_info(session, output, with_git=True)
        # Tombstoned pages document deleted/renamed files — never results.
        output = [item for item in output if item["page_id"] not in tombstoned]

        # Attach target_path to each item so the kind filter (path-prefix
        # heuristic) and downstream get_context callers can act on it.
        for item in output:
            item["target_path"] = page_info.get(item["page_id"], "")

        output = filter_dicts_by_key(output, "target_path", _get_exclude_spec(ctx.path))

        for item in output:
            # Freshness boost: recently-active files rank higher
            target_path = page_info.get(item["page_id"])
            gm = git_map.get(target_path) if target_path else None
            _apply_freshness_boost(item, gm)

        # Re-sort by boosted relevance, decisions hard-demoted on non-why queries
        _sort_demoting_decisions(output, query)

    output = _filter_by_kind(output, kind)
    output = output[:limit]

    # Derive confidence_score from relative position in the result set.
    _assign_confidence(output, "relevance_score", "confidence_score")

    response: dict = {"results": output, "_meta": _build_meta(repository=repository)}
    if grep_hint:
        response["grep_hint"] = grep_hint
    return response
