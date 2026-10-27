"""Symbol, path and hybrid search across one repo or a federation."""

from __future__ import annotations

from collections.abc import Container
from dataclasses import dataclass
from typing import Any

from repowise.core.persistence.database import get_session

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_search as _pkg
from repowise.server.mcp_server._federation import Federation, merge_ranked
from repowise.server.mcp_server._helpers import _get_repo
from repowise.server.mcp_server._hit_symbols import attach_hit_symbols
from repowise.server.mcp_server._meta import EXHAUSTIVE_SWEEP_HINT
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._page_paths import (
    file_candidates,
    hit_file_path,
)
from repowise.server.mcp_server._query_shape import (
    NOT_THE_NAMED_SYMBOL,
    _canonical_symbol_query,
    _embedded_identifiers,
    _has_exact_symbol,
    _identifier_candidates,
    _looks_like_code_name,
    _mark_not_the_named_symbol,
    _names_a_path,
)
from repowise.server.mcp_server._query_terms import content_terms
from repowise.server.mcp_server.tool_search_symbols import (
    _MAX_CANDIDATES,
    IssueFiles,
    file_path_index,
    search_paths_single,
)

from .ordering import (
    _FILE_WINDOW_OVERFETCH,
    _collapse_by_file,
    _hybrid_window,
    _protect_exact_paths,
    _protect_exact_symbols,
    _protect_named_symbols,
)
from .retrieval import _neg_score, _per_repo, _search_single_repo
from .serve import _page_language, _result_paths, _serve_snippets, _slim_served_rows


def _lead_candidates(response: dict, issue: IssueFiles | None, limit: int) -> None:
    """Trace files lead ``candidates``. A named identifier's defining file only
    fills a free slot after the ranked ones: ranked above them, it displaces
    better hits on ordinary questions. Capped at ``limit``."""
    if issue is None or not (issue.traced or issue.named):
        return
    ranked = [c["path"] for c in response.get("candidates") or []]
    paths = list(dict.fromkeys(issue.traced + ranked + issue.named))
    response["candidates"] = [{"path": p} for p in paths[:limit]]


def _tag_repo(items: list[dict], ctx, multi: bool) -> None:
    if multi:
        for item in items:
            item["repo"] = ctx.alias


def _missing_named_symbols(
    candidates: list[str], symbols: list[dict], canonical: bool, concepts: list[dict]
) -> list[str]:
    """Code-shaped names the query asks after that no returned symbol matches
    exactly (or returned module path carries): they do not exist here. Judged
    per name, so an indexed name beside a missing one hides nothing. Their
    fuzzy neighbours would stand in for them, so the caller keeps only exact
    symbols; the pages are marked here as related to the question, not the
    symbol."""
    if canonical:
        return []
    page_paths = [c.get("target_path") or "" for c in concepts]
    missing = [
        c
        for c in candidates
        if _looks_like_code_name(c)
        and not _has_exact_symbol([c], symbols)
        and not _names_a_path(c, page_paths)
    ]
    if missing:
        _mark_not_the_named_symbol(concepts)
    return missing


@dataclass(frozen=True)
class _SearchPlan:
    """One structured search: the caller's arguments plus the derived query."""

    query: str
    limit: int
    page_type: str | None
    kind: str | None
    symbol_kind: str | None
    mode: str
    names: Container[str] | None
    canonical_symbol: tuple[Any, str] | None
    symbol_query: str
    fetch: int
    multi: bool


@dataclass
class _Pools:
    """Each row kind merged and ranked across the repos searched."""

    contexts: list[Any]
    symbols: list[dict]
    spare: list[dict]
    files: list[dict]
    indexed_paths: list[str]
    concepts: list[dict]


def _symbol_query_for(
    query: str, mode: str, names: Container[str] | None, canonical_symbol: Any
) -> str:
    """The text the symbol scorer ranks on."""
    # Scored on the identifiers, not the prose: words like "is" would match
    # ``is_ci`` and bury the symbol the question names.
    symbol_query = query
    if canonical_symbol:
        symbol_query = canonical_symbol[1]
    if mode == "hybrid":
        _idents = _embedded_identifiers(query, names)
        # No identifier: content words, else the raw text.
        symbol_query = (
            " ".join(_idents) if _idents else " ".join(content_terms(query)) or query
        )
    return symbol_query


def _symbol_fetch(mode: str, limit: int) -> int:
    """How many symbol rows each repo fetches."""
    # Hybrid collapses rows per file, so it over-fetches; symbol mode takes every
    # candidate so ``fuzzy_omitted`` can count what an exact hit hides.
    fetch = limit * _FILE_WINDOW_OVERFETCH if mode == "hybrid" else limit
    if mode == "symbol":
        fetch = _MAX_CANDIDATES
    return fetch


async def _search_one_repo(
    ctx: Any, plan: _SearchPlan
) -> tuple[list[dict], list[dict], list[str], list[dict]]:
    """Symbol, path and page rows for one repo, plus its indexed paths in path mode."""
    s: list[dict] = []
    f: list[dict] = []
    paths: list[str] = []
    c: list[dict] = []
    if plan.mode in ("symbol", "hybrid"):
        s = await _pkg.search_symbols_single(
            ctx, plan.symbol_query, plan.fetch, symbol_kind=plan.symbol_kind, kind=plan.kind
        )
    if plan.mode == "path":
        f = await search_paths_single(ctx, plan.query, plan.limit)
        async with get_session(ctx.session_factory) as session:
            paths = (await file_path_index(session, (await _get_repo(session)).id)).paths
    if plan.mode == "hybrid":
        c = await _search_single_repo(ctx, plan.query, plan.limit, plan.page_type, plan.kind)
        for item in c:
            item["type"] = "page"
        await attach_hit_symbols(ctx, plan.query, c)
    for rows in (s, f, c):
        _tag_repo(rows, ctx, plan.multi)
    return s, f, paths, c


def _merge_pools(answered: list[tuple[Any, Any]], limit: int) -> _Pools:
    """Merge each repo's rows into one ranked pool per row kind."""
    parts = [part for _, part in answered]
    return _Pools(
        contexts=[ctx for ctx, _ in answered],
        symbols=merge_ranked((p[0] for p in parts), _neg_score("score"), per_repo_cap=limit),
        # Rows past each repo's limit only backfill slots that collapsing frees.
        spare=merge_ranked((p[0][limit:] for p in parts), _neg_score("score")),
        files=merge_ranked((p[1] for p in parts), _neg_score("score")),
        indexed_paths=[path for p in parts for path in p[2]],
        concepts=merge_ranked((p[3] for p in parts), _neg_score("relevance_score")),
    )


def _protect_exact(plan: _SearchPlan, pools: _Pools, candidates: list[str]) -> None:
    """Keep exact name and path matches ahead of their fuzzy neighbours."""
    if plan.mode == "symbol":
        pools.symbols = _protect_exact_symbols(plan.query, pools.symbols)
    elif plan.mode == "hybrid" and candidates:
        pools.symbols = _protect_named_symbols(candidates, pools.symbols)
    if plan.mode == "path":
        pools.files = _protect_exact_paths(plan.query, pools.files, pools.indexed_paths)


def _symbol_mode_results(
    plan: _SearchPlan, pools: _Pools, candidates: list[str], exact: bool
) -> tuple[list[dict], int]:
    """Symbol mode rows, and how many fuzzy names an exact hit hides."""
    # An exact hit is the answer; its fuzzy neighbours are only counted.
    fuzzy_omitted = 0
    pool = pools.symbols + pools.spare
    named = [s for s in pool if _has_exact_symbol(candidates, [s])] if exact else []
    if named:
        canonical_symbol = plan.canonical_symbol
        ident = (canonical_symbol[1] if canonical_symbol else plan.query).strip().lower()
        shown = {id(s) for s in named}
        fuzzy_omitted = sum(
            1 for s in pool if id(s) not in shown and ident in (s.get("name") or "").lower()
        )
    return (named or pools.symbols)[: plan.limit], fuzzy_omitted


def _hybrid_mode_results(plan: _SearchPlan, pools: _Pools, exact: bool) -> list[dict]:
    """Hybrid rows: symbol matches interleaved with concept pages for new files."""
    window = _hybrid_window(
        plan.query, pools.symbols, pools.concepts, plan.limit, exact, plan.names
    )
    # One row per file; freed slots fill with the next pages, then symbols.
    served = {id(item) for item in window}
    rest = [
        item for item in pools.concepts + pools.symbols + pools.spare if id(item) not in served
    ]
    results = _collapse_by_file(window + rest)[: plan.limit]
    # Non-file pages leave the window unrefilled unless the caller asked for
    # pages by type or kind="doc".
    if not plan.page_type and plan.kind != "doc":
        results = [item for item in results if hit_file_path(item)]
    return results


def _select_results(
    plan: _SearchPlan, pools: _Pools, candidates: list[str], exact: bool
) -> tuple[list[dict], int]:
    """The served rows for the plan's mode, and the symbol-mode fuzzy count."""
    if plan.mode == "symbol":
        return _symbol_mode_results(plan, pools, candidates, exact)
    if plan.mode == "path":
        return pools.files[: plan.limit], 0
    return _hybrid_mode_results(plan, pools, exact), 0


async def _single_repository(contexts: list[Any], multi: bool) -> Any:
    """The repository row a single-repo search ran against, else None."""
    if multi or not contexts:
        return None
    async with get_session(contexts[0].session_factory) as session:
        return await _get_repo(session)


def _exact_match_note(
    candidates: list[str],
    exact: bool,
    missing: list[str],
    fuzzy_omitted: int,
    has_results: bool,
    grep_hint: str | None,
) -> str | None:
    """The note qualifying an identifier query's results, if one is owed."""
    if missing:
        shown = ", ".join(repr(c) for c in missing[:3])
        # An empty window's grep_hint already carries the sweep advice.
        pages = f" Any page here is {NOT_THE_NAMED_SYMBOL}." if has_results else ""
        sweep = "" if (grep_hint and not has_results) else " " + EXHAUSTIVE_SWEEP_HINT
        return (
            f"No indexed symbol is named {shown}, so no symbol is returned "
            f"for it.{pages} Recheck the spelling, or search a shorter part "
            f"of the name.{sweep}"
        )
    if not exact:
        shown = ", ".join(repr(c) for c in candidates[:3])
        return (
            f"No indexed symbol exactly matches {shown}. The results are "
            "fuzzy neighbours ranked by token overlap — confirm a hit names "
            "what you meant before relying on it. If you expected an exact "
            "symbol, recheck spelling/casing. " + EXHAUSTIVE_SWEEP_HINT
        )
    if fuzzy_omitted:
        return (
            f"{fuzzy_omitted} other symbols contain this name; search a "
            "longer or partial name to list them."
        )
    return None


def _match_named(
    plan: _SearchPlan, pools: _Pools, candidates: list[str]
) -> tuple[bool, list[str]]:
    """Whether a returned symbol names the query exactly, and the names none does.

    Missing names leave only exact symbols in *pools*.
    """
    exact = _has_exact_symbol(candidates, pools.symbols) if candidates else False
    missing = _missing_named_symbols(
        candidates, pools.symbols, bool(plan.canonical_symbol), pools.concepts
    )
    if missing:
        pools.symbols = [s for s in pools.symbols if _has_exact_symbol(candidates, [s])]
        pools.spare = [s for s in pools.spare if _has_exact_symbol(candidates, [s])]
    return exact, missing


def _attach_match_notes(
    response: dict,
    candidates: list[str],
    exact: bool,
    missing: list[str],
    grep_hint: str | None,
) -> None:
    """Exact-match flag, its note, and the grep fallback for an empty window."""
    results = response["results"]
    # Fuzzy neighbours of a missing name look authoritative, so say so.
    if candidates:
        response["exact_match"] = exact and not missing
        note = _exact_match_note(
            candidates,
            exact,
            missing,
            response.get("fuzzy_omitted", 0),
            bool(results),
            grep_hint,
        )
        if note:
            response["note"] = note
    if grep_hint and not results:
        response["grep_hint"] = grep_hint


async def _structured_search(
    query: str,
    limit: int,
    page_type: str | None,
    kind: str | None,
    symbol_kind: str | None,
    repo: str | None,
    mode: str,
    grep_hint: str | None,
    names: Container[str] | None = None,
    fed: Federation | None = None,
) -> dict:
    """Run symbol / path / hybrid search, across repos when federated, and shape the response.

    ``grep_hint`` is attached only when nothing was found.
    """
    multi = fed is not None and fed.multi
    canonical_symbol = _canonical_symbol_query(query)
    plan = _SearchPlan(
        query=query,
        limit=limit,
        page_type=page_type,
        kind=kind,
        symbol_kind=symbol_kind,
        mode=mode,
        names=names,
        canonical_symbol=canonical_symbol,
        symbol_query=_symbol_query_for(query, mode, names, canonical_symbol),
        fetch=_symbol_fetch(mode, limit),
        multi=multi,
    )

    async def _one_repo(ctx) -> tuple[list[dict], list[dict], list[str], list[dict]]:
        return await _search_one_repo(ctx, plan)

    pools = _merge_pools(await _per_repo(repo, fed, _one_repo), limit)
    candidates = _identifier_candidates(query, mode, names)
    _protect_exact(plan, pools, candidates)
    exact, missing = _match_named(plan, pools, candidates)

    results, fuzzy_omitted = _select_results(plan, pools, candidates, exact)
    repository = await _single_repository(pools.contexts, multi)

    response: dict = {
        "results": results,
        "mode": mode,
        "_meta": _build_meta(repository=repository, targets=_result_paths(results)),
    }
    if fuzzy_omitted:
        response["fuzzy_omitted"] = fuzzy_omitted
    # Not ``candidates``: that name holds the query's identifiers the note quotes.
    if file_cands := file_candidates(results, limit=limit):
        response["candidates"] = file_cands
    _attach_match_notes(response, candidates, exact, missing, grep_hint)
    if fed is not None:
        fed.annotate(response, results)
    # Last: slims fields the steps above still read.
    _slim_served_rows(
        results,
        {(ctx.alias if multi else None): _page_language(ctx) for ctx in pools.contexts},
    )
    _serve_snippets(results)
    return response
