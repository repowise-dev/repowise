"""MCP Tool 5: search_codebase — semantic search over the wiki."""

from __future__ import annotations

import logging
from typing import Any

from repowise.core.persistence.database import get_session
from repowise.core.registry import ToolRecipe
from repowise.core.registry import mcp_tool_registry as mcp

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_search as _pkg
from repowise.server.mcp_server._federation import Federation
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _resolve_repo_context,
    attach_ignored_arguments,
    filter_dicts_by_key,
    resolve_enum_argument,
)
from repowise.server.mcp_server._hit_symbols import attach_hit_symbols
from repowise.server.mcp_server._line_hits import attach_line_hits
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._page_paths import file_candidates
from repowise.server.mcp_server._query_shape import (
    _VALID_MODES,
    _fetch_limit_for,
    _resolve_mode,
)
from repowise.server.mcp_server._retrieval_rank import (
    boost_named_paths,
    rerank_pages_first,
)
from repowise.server.mcp_server.tool_search_symbols import (
    IssueFiles,
    file_path_index,
    indexed_names,
    issue_files,
)

from .ordering import _collapse_by_file
from .ranking import (
    _VALID_KINDS,
    _dedup_decisions,
    _downweight_decisions,
    _downweight_test_pages,
    _filter_by_kind,
    _rescue_all_decision_window,
    _reserve_symbol_tail,
    _sort_demoting_noise,
)
from .retrieval import _federated_search, _fused_retrieve, _per_repo
from .serve import (
    _apply_freshness_boost,
    _assign_confidence,
    _attach_paths,
    _drop_derivable_confidence,
    _drop_internal_ranking_fields,
    _grep_hint_for,
    _load_page_info,
    _page_language,
    _result_paths,
    _serve_snippets,
    _slim_served_rows,
)
from .structured import _structured_search

_log = logging.getLogger("repowise.mcp.search")


def _query_from_pattern(query: str, pattern: str | None, ignored: list[dict[str, Any]]) -> str:
    """``pattern`` fills an empty ``query``; beside a real one it is reported ignored."""
    # Hosts that defer tool schemas let a model guess grep's argument name.
    if pattern is not None and not query.strip():
        return pattern
    if pattern is not None:
        ignored.append(
            {"argument": "pattern", "values": [pattern], "valid": [], "superseded_by": "query"}
        )
    return query


def _resolve_kind_and_mode(
    kind: str | None, mode: str, ignored: list[dict[str, Any]]
) -> tuple[str | None, str]:
    """Validate ``kind`` and ``mode``; an unknown value is dropped and reported."""
    kind = resolve_enum_argument(kind, _VALID_KINDS, argument="kind", ignored=ignored)
    # Modes are case-insensitive; only a genuine miss is dropped, under its own spelling.
    if mode is not None and mode.lower() in _VALID_MODES:
        mode = mode.lower()
    mode = resolve_enum_argument(mode, _VALID_MODES, argument="mode", ignored=ignored)
    return kind, mode


async def _indexed_names(repo: str | None, fed: Federation | None, query: str) -> set[str]:
    """Indexed names the query mentions, loaded once so every stage validates alike."""
    names: set[str] = set()
    for _, repo_names in await _per_repo(repo, fed, lambda ctx: indexed_names([ctx], query)):
        names |= repo_names
    return names


async def _issue_files(repo: str | None, query: str, names: set[str]) -> IssueFiles | None:
    """Files the query traces or names; single repo only (a federation has no one tree)."""
    if repo == "all":
        return None
    try:
        return await issue_files(await _resolve_repo_context(repo), query, names)
    except Exception:
        _log.warning("search_codebase: issue file lookup failed", exc_info=True)
    return None


async def _rank_concept_pages(ctx: Any, output: list[dict], query: str, repo_id: Any) -> list[dict]:
    """Drop tombstoned and excluded pages, then boost, demote and dedupe the rest."""
    async with get_session(ctx.session_factory) as session:
        page_info, tombstoned, git_map = await _load_page_info(session, output, with_git=True)
        path_index = await file_path_index(session, repo_id)
    # Tombstoned pages document deleted/renamed files — never results.
    output = [item for item in output if item["page_id"] not in tombstoned]
    _attach_paths(output, page_info)
    output = filter_dicts_by_key(output, "target_path", _pkg._get_exclude_spec(ctx.path))
    for item in output:
        target_path = page_info.get(item["page_id"])
        _apply_freshness_boost(item, git_map.get(target_path) if target_path else None)
    output = rerank_pages_first(output, query, score_key="relevance_score", floor=0.5)
    # After the coverage rerank, whose window-relative weights cannot tell
    # a word rare across the repo from one rare in these few hits.
    boost_named_paths(
        output, query, path_index.word_counts, len(path_index.paths), score_key="relevance_score"
    )
    _downweight_test_pages(output, query)
    _sort_demoting_noise(output, query)
    return _dedup_decisions(output)


async def _concept_search(
    query: str,
    limit: int,
    page_type: str | None,
    kind: str | None,
    repo: str | None,
    issue: IssueFiles | None,
    grep_hint: str | None,
) -> tuple[dict, Any]:
    """Single-repo concept search; returns the response and the repo context it read."""
    ctx = await _resolve_repo_context(repo)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
    await _pkg._wait_for_vector_store(ctx)

    # Over-fetch: post-filters trim hits, and decision down-weighting needs file
    # pages inside the window to promote.
    fetch_limit = _fetch_limit_for(limit, kind)
    output = await _fused_retrieve(ctx, query, fetch_limit, page_type)
    _downweight_decisions(output, query)
    output = await _rescue_all_decision_window(ctx, output, query, fetch_limit)
    if output:
        output = await _rank_concept_pages(ctx, output, query, repository.id)

    output = _filter_by_kind(output, kind)
    # A file page and a symbol page of one file are one place to look.
    output = _collapse_by_file(output)
    # Files the page retrievers cannot see (a private helper, a local name) get
    # the weakest tail slots.
    output = _reserve_symbol_tail(output, limit, kind)
    _drop_internal_ranking_fields(output)
    # The full ranked pool, kept so ``candidates`` can reach past the cut.
    ranked = list(output)
    output = output[:limit]
    await attach_hit_symbols(ctx, query, output)
    _assign_confidence(output, "relevance_score", "confidence_score")

    response: dict = {
        "results": output,
        "_meta": _build_meta(repository=repository, targets=_result_paths(output)),
    }
    if candidates := file_candidates(ranked, limit=limit):
        response["candidates"] = candidates
    _pkg._lead_candidates(response, issue, limit)
    if grep_hint and not output:
        response["grep_hint"] = grep_hint
    return response, ctx


@mcp.tool(
    surface_order=40,
    artifact_type="search_results",
    presentation="search_results",
    evidence_basis="inferred",
    recipes=(
        ToolRecipe(
            "find_raw_hits",
            'search_codebase(query="identifier or path", mode="auto")',
            ("search_codebase",),
        ),
    ),
)
async def search_codebase(
    query: str = "",
    limit: int = 5,
    page_type: str | None = None,
    kind: str | None = None,
    repo: str | None = None,
    mode: str = "auto",
    symbol_kind: str | None = None,
    pattern: str | None = None,
) -> dict:
    """Find code by concept, symbol, or path — hybrid codebase search.

    For questions (how/where/why), call get_answer instead: it runs this
    retrieval internally and returns a cited answer. Use this for raw
    ranked hits: enumerating matches, resolving an identifier to a symbol_id,
    or scoping get_context.

    mode="auto" routes by query shape: identifiers search symbols (symbol_id,
    path, line bounds for get_symbol), paths resolve files (for get_context),
    prose runs wiki-semantic search, and mixed queries run hybrid (symbol hits,
    then file-backed pages only). Decision records rank below file pages
    unless the query is why-shaped.

    Rows naming a file carry `path`; concept pages add `symbols`
    (name:line) the query matches; the top 3 add `matched_lines`
    (line, text), sample lines sharing its words. `candidates` lists up to `limit`
    distinct files to Read, best first. Identifier or literal queries also
    return `lines` (path, line, kind, text; definitions first) read from live
    files; when `complete` is true they are every match, so no grep is needed.

    Args:
        query: identifier, path, or natural language.
        limit: max results (default 5); distinct files outside mode="symbol"
            (same-file symbols in `symbols`).
        page_type: file_page (per-file docs) or module_page (subsystem
            pages); any stored page type filters.
        kind: implementation | test | config | doc (concept/symbol modes).
        repo: alias, or "all" for workspace-wide.
        mode: auto | concept | symbol | path | hybrid.
        symbol_kind: filter symbol hits (function|class|method|...).
        pattern: alias for query.
    """
    ignored: list[dict[str, Any]] = []
    query = _query_from_pattern(query, pattern, ignored)
    if not query.strip():
        return {
            "results": [],
            "error": "search_codebase requires `query` (or its alias `pattern`).",
            "_meta": _build_meta(),
        }
    kind, mode = _resolve_kind_and_mode(kind, mode, ignored)
    # One fan-out state for the whole call: a repo that fails one stage is
    # skipped by the next and reported once.
    fed = Federation() if repo == "all" else None
    names = await _indexed_names(repo, fed, query)
    grep_hint = _grep_hint_for(query, names)
    resolved_mode = _resolve_mode(query, mode, names)
    issue = await _issue_files(repo, query, names)

    if resolved_mode in ("symbol", "path", "hybrid"):
        structured = await _structured_search(
            query, limit, page_type, kind, symbol_kind, repo, resolved_mode, grep_hint, names, fed
        )
        _pkg._lead_candidates(structured, issue, limit)
        await attach_line_hits(structured, query, resolved_mode, names, repo)
        attach_ignored_arguments(structured, ignored)
        return structured

    if fed is not None:
        # kind is filtered per repo before each repo's limit cut, so the fused
        # list is already kind-pure and full.
        federated = await _federated_search(query, limit, page_type, kind, fed)
        if grep_hint and not federated.get("results"):
            federated["grep_hint"] = grep_hint
        attach_ignored_arguments(federated, ignored)
        return federated

    response, ctx = await _concept_search(query, limit, page_type, kind, repo, issue, grep_hint)
    await attach_line_hits(response, query, resolved_mode, names, repo)
    attach_ignored_arguments(response, ignored)
    # Last, so nothing above has to know the field is on its way out.
    _slim_served_rows(response["results"], {None: _page_language(ctx)})
    _drop_derivable_confidence(response["results"])
    _serve_snippets(response["results"])
    return response
