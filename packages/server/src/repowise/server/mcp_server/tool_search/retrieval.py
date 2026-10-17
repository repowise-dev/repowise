"""Fused page retrieval for one repo and across a workspace."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from repowise.core.persistence.database import get_session

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_search as _pkg
from repowise.server.mcp_server._answer_pipeline import (
    _FILENAME_LEG_RRF_K,
    _RRF_K,
    _RRF_SCORE_SCALE,
    _SYMBOL_LEG_RRF_K,
    leg_ranks,
)
from repowise.server.mcp_server._federation import Federation, merge_ranked
from repowise.server.mcp_server._helpers import (
    _VECTOR_TIMEOUT_ENV,
    _get_repo,
    _resolve_repo_context,
    filter_dicts_by_key,
    vector_search_timeout_s,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._page_paths import (
    PAGELESS_FILE,
    file_candidates,
    file_path_of,
    pageless_path,
)
from repowise.server.mcp_server._query_shape import (
    _MIN_RELEVANCE_SCORE,
    _fetch_limit_for,
)
from repowise.server.mcp_server._retrieval_rank import boost_named_paths
from repowise.server.mcp_server.tool_search_symbols import file_path_index

from .ordering import _collapse_by_file
from .ranking import (
    _dedup_decisions,
    _downweight_decisions,
    _downweight_test_pages,
    _filter_by_kind,
    _rescue_all_decision_window,
    _sort_demoting_noise,
)
from .serve import (
    _assign_confidence,
    _attach_paths,
    _load_page_info,
    _page_language,
    _serve_snippets,
    _slim_served_rows,
)

_log = logging.getLogger("repowise.mcp.search")


async def _wait_for_vector_store(ctx) -> None:
    """Block (bounded) until the vector store signals readiness, if it tracks it."""
    if ctx.vector_store_ready is not None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ctx.vector_store_ready.wait(), timeout=30.0)


async def _safe_fts(ctx, query: str, limit: int) -> list:
    """FTS search, bounded and failure-swallowing (returns [] on error/timeout)."""
    if ctx.fts is None:
        return []
    with contextlib.suppress(Exception):
        return await asyncio.wait_for(ctx.fts.search(query, limit=limit), timeout=5.0)
    return []


async def _safe_vector(ctx, query: str, limit: int) -> list:
    """Vector search, bounded and failure-swallowing (returns [] on error/timeout).

    Readiness is awaited by the caller (``_wait_for_vector_store``) before the
    fused retrieve runs, so this path does not re-wait.

    Returns nothing on a keyless index. ``store_has_semantic_vectors`` explains
    why the mock's vectors cannot be ranked on; the reason this is enforced here
    rather than in ``_fused_retrieve`` is that this function is the only place
    the vector leg is entered, so a later caller inherits the guard.
    """
    if ctx.vector_store is None or not _pkg.store_has_semantic_vectors(ctx.vector_store):
        return []
    try:
        return await asyncio.wait_for(
            ctx.vector_store.search(query, limit=limit), timeout=vector_search_timeout_s()
        )
    except TimeoutError:
        # Logged, not silent: a timeout drops the whole semantic leg and the
        # full-text-only hits that remain look like a normal result set.
        _log.warning(
            "Vector search exceeded its %gs budget; returning no semantic hits. "
            "Raise it with %s=<seconds>.",
            vector_search_timeout_s(),
            _VECTOR_TIMEOUT_ENV,
        )
    except Exception:
        _log.debug("Vector search failed; returning no semantic hits", exc_info=True)
    return []


def _symbol_leg_target(fused: dict[str, dict], page_id: str) -> str:
    """The fused page a symbol-leg file page credits: the best page of its file.

    The leg names files, while the page legs may have reached the same file
    through a symbol page. Crediting only the file page id would leave that
    hit unlifted and add a weak twin that collapsing then drops.
    """
    path = page_id.partition(":")[2]
    best = None
    for pid, entry in fused.items():
        if file_path_of(entry["page_type"], pid.partition(":")[2]) != path:
            continue
        if best is None or entry["_rrf"] > fused[best]["_rrf"]:
            best = pid
    return best or page_id


def _fused_entry(r) -> dict:
    """Seed a fused-result dict from a retriever hit (RRF score added by caller)."""
    entry = {
        "page_id": r.page_id,
        "title": r.title,
        "page_type": r.page_type,
        "snippet": r.snippet,
        "_rrf": 0.0,
        "_sources": set(),
    }
    if r.page_type == PAGELESS_FILE:
        # No page row to load it from later.
        entry["target_path"] = pageless_path(r.page_id)
    return entry


async def _fused_retrieve(ctx, query: str, fetch_limit: int, page_type: str | None) -> list[dict]:
    """Retrieve by fusing FTS, vector and symbol search via Reciprocal Rank Fusion.

    The retrievers run in parallel; a hit's score is the sum of
    ``1/(rank + k)`` over the retrievers that surfaced it, scaled into the
    BM25 range the downstream relevance gates were tuned against. Each hit
    carries a ``sources`` list (``"fts"``, ``"vector"``, ``"symbol"``,
    ``"filename"``): a page found by several retrievers is a stronger match
    than one found by one mode. The symbol and filename legs also name files
    that have no page, as ``page_type: "file"`` rows.

    This mirrors get_answer's ``hybrid_retrieve`` so the two entry points rank
    the same corpus identically. The prior path ran vector search and fell
    back to FTS only when vector returned nothing, so a page FTS ranked highly
    but vector missed never surfaced here.

    A hit is admitted only when its raw retriever score clears
    ``_MIN_RELEVANCE_SCORE`` — the fused score is rank-based and would
    otherwise sit well above the floor for every window hit, letting an
    off-topic query return its nearest-but-unrelated neighbours. Rank position
    (for RRF) is the hit's place in the retriever's own list, unchanged by the
    floor.
    """
    fts_results, vec_results, sym_results, name_results = await asyncio.gather(
        _pkg._safe_fts(ctx, query, fetch_limit),
        _pkg._safe_vector(ctx, query, fetch_limit),
        _pkg._safe_symbol_search(ctx, query, pageless=True),
        _pkg._safe_filename_search(ctx, query),
    )

    fused: dict[str, dict] = {}
    for rank, r in enumerate(vec_results):
        if r.score < _MIN_RELEVANCE_SCORE:
            continue
        entry = fused.setdefault(r.page_id, _fused_entry(r))
        entry["_rrf"] += 1.0 / (rank + _RRF_K)
        entry["_sources"].add("vector")
    for rank, r in enumerate(fts_results):
        if r.score < _MIN_RELEVANCE_SCORE:
            continue
        entry = fused.setdefault(r.page_id, _fused_entry(r))
        entry["_rrf"] += 1.0 / (rank + _RRF_K)
        entry["_sources"].add("fts")
    # Files whose symbol names the query's words spell, paged or not. Weighted
    # below the page legs, as in get_answer; it has no raw score to floor.
    for rank, r in leg_ranks(sym_results):
        entry = fused.setdefault(_symbol_leg_target(fused, r.page_id), _fused_entry(r))
        entry["_rrf"] += 1.0 / (rank + _SYMBOL_LEG_RRF_K)
        entry["_sources"].add("symbol")
    # Files whose file name the query's words spell, weighted as a name match.
    # It adds files the other legs missed and never reorders a page they found.
    # A name of three or more words the query spells in full weighs like a page hit.
    for rank, (r, full) in leg_ranks(name_results):
        target = _symbol_leg_target(fused, r.page_id)
        if target in fused and r.page_type != PAGELESS_FILE:
            continue
        entry = fused.setdefault(target, _fused_entry(r))
        entry["_rrf"] += 1.0 / (rank + (_RRF_K if full else _FILENAME_LEG_RRF_K))
        entry["_sources"].add("filename")

    output: list[dict] = []
    for entry in fused.values():
        if page_type and entry["page_type"] != page_type:
            continue
        entry["relevance_score"] = round(entry.pop("_rrf") * _RRF_SCORE_SCALE, 4)
        entry["sources"] = sorted(entry.pop("_sources"))
        output.append(entry)
    output.sort(key=lambda item: item["relevance_score"], reverse=True)
    return output


async def _search_single_repo(
    ctx, query: str, limit: int, page_type: str | None, kind: str | None = None
) -> list[dict]:
    """Run search against a single repo context.

    Fuses FTS and vector retrieval via RRF (see ``_fused_retrieve``), so each
    hit carries a ``sources`` list naming the retrievers that surfaced it
    rather than a single backend label.

    The ``kind`` filter runs here, on the over-fetched list and BEFORE the
    limit cut — filtering after per-repo truncation returned fewer than
    ``limit`` results (frequently zero) in the federated path.
    """
    await _pkg._wait_for_vector_store(ctx)

    fetch_limit = _fetch_limit_for(limit, kind)
    output = await _fused_retrieve(ctx, query, fetch_limit, page_type)

    _downweight_decisions(output, query)
    output = await _rescue_all_decision_window(ctx, output, query, fetch_limit)

    # Attach target_path and drop excluded hits per repo (so federated search
    # honours each repo's own exclude_patterns) before ranking. Runs on the
    # over-fetched list so the kind filter below has real headroom. Load
    # precedes the sort/demotion so they see the page metadata they key on
    # (test demotion needs target_path to classify a test file page).
    if output:
        async with get_session(ctx.session_factory) as session:
            page_info, tombstoned, _ = await _load_page_info(session, output)
            path_index = await file_path_index(session, (await _get_repo(session)).id)
        output = [item for item in output if item["page_id"] not in tombstoned]
        _attach_paths(output, page_info)
        output = filter_dicts_by_key(output, "target_path", _pkg._get_exclude_spec(ctx.path))
        boost_named_paths(
            output, query, path_index.word_counts, len(path_index.paths), score_key="relevance_score"
        )

    _downweight_test_pages(output, query)
    _sort_demoting_noise(output, query)
    output = _dedup_decisions(output)

    output = _filter_by_kind(output, kind)
    return _collapse_by_file(output)[:limit]


def _neg_score(key: str):
    """Best-first sort key on a row's *key* score, for :func:`merge_ranked`."""
    return lambda item: -(item.get(key) or 0.0)


async def _federated_search(
    query: str, limit: int, page_type: str | None, kind: str | None, fed: Federation
) -> dict:
    """Search across all repos using Reciprocal Rank Fusion."""

    async def _one_repo(ctx) -> list[dict]:
        repo_results = await _search_single_repo(ctx, query, limit, page_type, kind)
        for rank, item in enumerate(repo_results):
            item["repo"] = ctx.alias
            item["rrf_score"] = 1.0 / (rank + 60)  # RRF constant k=60
        return repo_results

    answered = await fed.run(_one_repo)
    contexts = [ctx for ctx, _ in answered]
    all_results = merge_ranked(
        (rows for _, rows in answered), _neg_score("rrf_score"), per_repo_cap=limit
    )
    output = all_results[:limit]

    # Derive confidence from RRF position
    _assign_confidence(output, "rrf_score", "confidence_score")

    # No single indexed commit to compare a live HEAD against: per-repo
    # freshness rides on ``repos_stale`` and the rows' ``stale`` flag instead.
    response: dict = {"results": output, "_meta": _build_meta()}
    fed.annotate(response, output)
    # Drawn from every repo's ranked list, not the merged cut: a workspace
    # search that spends its window on module pages should still be able to
    # name files.
    if candidates := file_candidates(all_results, limit=limit):
        response["candidates"] = candidates
    # Last, so nothing above has to know the field is on its way out.
    _slim_served_rows(output, {ctx.alias: _page_language(ctx) for ctx in contexts})
    _serve_snippets(output)
    return response


async def _per_repo(repo: str | None, fed: Federation | None, fn) -> list[tuple[Any, Any]]:
    """``(context, fn(context))`` for the one repo asked for, or every repo via *fed*."""
    if fed is not None:
        return await fed.run(fn)
    ctx = await _resolve_repo_context(repo)
    return [(ctx, await fn(ctx))]
