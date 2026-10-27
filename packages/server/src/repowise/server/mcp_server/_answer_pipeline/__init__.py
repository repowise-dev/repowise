"""Retrieval pipeline for ``get_answer``: question in, ranked wiki hits out.

The LLM step, cache and response shape live in ``tool_answer``. Stages, each a
function over hit dicts:

1. ``hybrid_retrieve``: FTS, vector, symbol and filename legs in parallel,
   fused by Reciprocal Rank Fusion. The page legs only see what a generated
   page spells out; the symbol and filename legs recover private helpers and
   local names.
2. ``hydrate_hits`` (``hydrate``): attach path, summary and page type.
3. ``apply_pagerank_bias`` (``hydrate``): central files win near-ties.
4. ``expand_via_graph`` (``graph_expand``): add 1-hop neighbours of the top hits.
5. ``expand_via_parent_page`` (``parent_page``): a subsystem question leads with
   its concept page.

``noise`` demotes decision and test pages; ``vectors`` and ``leg_record`` are
shared by the legs. The legs and their merge live here, so a leg replaced on
this module is the one ``hybrid_retrieve`` calls.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from repowise.core.analysis.decisions.semantic_match import DECISION_VECTOR_PREFIX
from repowise.core.providers.embedding import store_has_semantic_vectors
from repowise.server.mcp_server._answer_pipeline.graph_expand import (
    _GRAPH_EXPAND_DAMPING,
    _GRAPH_EXPAND_MAX_NEW,
    _GRAPH_EXPAND_TOP_N,
    _HUB_DEGREE_FLOOR,
    _HUB_DEGREE_PERCENTILE,
    _hub_degree_cutoff,
    _projected_edges,
    _projected_pair,
    expand_via_graph,
)
from repowise.server.mcp_server._answer_pipeline.hydrate import (
    _PAGERANK_BIAS_MAX,
    apply_pagerank_bias,
    hydrate_hits,
)
from repowise.server.mcp_server._answer_pipeline.leg_record import (
    _LEG_RECORD,
    _record_leg,
    begin_leg_record,
    degraded_legs,
    retrieval_legs,
)
from repowise.server.mcp_server._answer_pipeline.noise import _TEST_QUERY_RE, demote_noise_hits
from repowise.server.mcp_server._answer_pipeline.parent_page import (
    _CONCEPT_FETCH_LIMIT,
    _PARENT_EXPAND_BOOST,
    _PARENT_EXPAND_TOP_N,
    _PARENT_MIN_SHARE,
    _SUBSYSTEM_QUERY_RE,
    _common_ancestor,
    _semantic_concept_paths,
    expand_via_parent_page,
    is_subsystem_query,
)
from repowise.server.mcp_server._answer_pipeline.vectors import (
    _QUESTION_VECTOR_CACHE_MAX,
    _QUESTION_VECTORS,
    question_vector,
    vector_search,
)
from repowise.server.mcp_server._helpers import _VECTOR_TIMEOUT_ENV, vector_search_timeout_s
from repowise.server.mcp_server._page_paths import PAGELESS_FILE
from repowise.server.mcp_server._prose_symbols import filename_backed_pages, symbol_backed_pages

_log = logging.getLogger("repowise.mcp.answer")

# Candidates each leg fetches before fusion.
_RETRIEVAL_FETCH_LIMIT = 15

# Decision vectors share the page store under this page-id prefix.
_DECISION_PREFIX = DECISION_VECTOR_PREFIX

# Extra rows the vector leg fetches so dropping decision rows still leaves a
# full window; decision rows rank well on question-shaped text.
_DECISION_OVERFETCH = 15

# Standard RRF k: adjacent ranks score close, a one-leg rank-10 hit still counts.
_RRF_K = 60

# Scales fused scores into the BM25 range the downstream confidence gates were
# tuned on (top RRF 1/60 + 1/60 ~ 0.033 -> ~6). Never changes the order.
_RRF_SCORE_SCALE = 180.0

# Symbol leg size: fewer than the page legs, it adds what they cannot see.
_SYMBOL_LEG_FETCH_LIMIT = 20
_SYMBOL_LEG_MAX_PAGES = 8

# A larger k for name-match legs: they can lift or add a file, never outvote
# two page legs that agree.
_SYMBOL_LEG_RRF_K = 180

# A name the question spells in full (``full_cover``) fuses at the page legs' k.
_FILENAME_LEG_RRF_K = _SYMBOL_LEG_RRF_K


async def hybrid_retrieve(question: str, ctx: Any) -> list[dict]:
    """Run the four legs in parallel and fuse them by RRF, best first.

    Hits are ``{page_id, title, score, snippet, page_type, _sources}`` plus each
    leg's rank (``_fts_rank``, ``_vec_rank``, ``_sym_rank``): RRF compresses
    scores, and confidence reads leg agreement from the ranks. A leg that fails
    or times out contributes nothing. Pageless files come back as
    ``page_type: "file"`` hits.
    """
    # Reset so the record describes this question, not one sharing the context.
    begin_leg_record()
    fts_task = _safe_fts_search(ctx, question)
    vec_task = _safe_vector_search(ctx, question)
    sym_task = _safe_symbol_search(ctx, question, pageless=True)
    name_task = _safe_filename_search(ctx, question)
    fts_results, vec_results, sym_results, name_results = await asyncio.gather(
        fts_task, vec_task, sym_task, name_task
    )

    fused: dict[str, dict] = {}
    for rank, h in enumerate(fts_results):
        entry = fused.setdefault(h.page_id, _hit_dict_from_result(h))
        entry["score"] = entry.get("score", 0.0) + 1.0 / (rank + _RRF_K)
        entry["_sources"].add("fts")
        entry["_fts_rank"] = rank
    for rank, h in enumerate(vec_results):
        entry = fused.setdefault(h.page_id, _hit_dict_from_result(h))
        entry["score"] = entry.get("score", 0.0) + 1.0 / (rank + _RRF_K)
        entry["_sources"].add("vector")
        entry["_vec_rank"] = rank
    for rank, h in leg_ranks(sym_results):
        entry = fused.setdefault(h.page_id, _hit_dict_from_result(h))
        entry["score"] = entry.get("score", 0.0) + 1.0 / (rank + _SYMBOL_LEG_RRF_K)
        entry["_sources"].add("symbol")
        entry["_sym_rank"] = rank
        entry["_symbol_names"] = h.symbol_names
    # Adds files the other legs missed; never reorders a page they found.
    for rank, (h, full) in leg_ranks(name_results):
        if h.page_id in fused and h.page_type != PAGELESS_FILE:
            continue
        entry = fused.setdefault(h.page_id, _hit_dict_from_result(h))
        entry["score"] = entry.get("score", 0.0) + 1.0 / (
            rank + (_RRF_K if full else _FILENAME_LEG_RRF_K)
        )
        entry["_sources"].add("filename")

    for entry in fused.values():
        entry["score"] = entry["score"] * _RRF_SCORE_SCALE

    merged = list(fused.values())
    merged.sort(key=lambda h: h["score"], reverse=True)
    return merged


def stamp_hybrid_rank(hits: list[dict]) -> None:
    """Record each hit's 0-based file rank in the fused order, before any rerank.

    Symbol pages share their file's rank; a pageless file shares the rank of the
    page below it, so it never pushes a page down.
    """
    files: dict[str, int] = {}
    for h in hits:
        path = (h.get("target_path") or "").split("::", 1)[0]
        if h.get("page_type") == PAGELESS_FILE:
            h["_hybrid_rank"] = len(files)
        else:
            h["_hybrid_rank"] = files.setdefault(path, len(files))


async def _safe_fts_search(ctx: Any, question: str) -> list[Any]:
    """FTS leg; [] on timeout or failure."""
    if ctx.fts is None:
        _record_leg("fts", "absent")
        return []
    try:
        results = await asyncio.wait_for(
            ctx.fts.search(question, limit=_RETRIEVAL_FETCH_LIMIT), timeout=5.0
        )
    except TimeoutError:
        _record_leg("fts", "timeout")
        return []
    except Exception:
        _record_leg("fts", "error")
        return []
    _record_leg("fts", "ok")
    return results


def _pages_only(results: list[Any], limit: int) -> list[Any]:
    """Best-first *results* without decision vectors, capped at *limit*.

    A decision row has no page, so it could not be cited, scoped or tombstoned.
    Why-shaped questions get decisions from ``decision_records`` instead.
    """
    kept = [r for r in results if not str(getattr(r, "page_id", "")).startswith(_DECISION_PREFIX)]
    dropped = len(results) - len(kept)
    if dropped:
        _log.debug(
            "get_answer page retrieval dropped %d decision vector(s) from a window of %d",
            dropped,
            len(results),
        )
    if len(kept) < limit and dropped:
        # A short window would otherwise read as thin retrieval.
        _log.warning(
            "get_answer page retrieval kept only %d of a requested %d candidates: %d "
            "decision vector(s) filled the window. Decisions may be over-represented "
            "in the vector store.",
            len(kept),
            limit,
            dropped,
        )
    return kept[:limit]


async def _safe_vector_search(ctx: Any, question: str) -> list[Any]:
    """Vector leg; [] on a keyless store, timeout or failure.

    Waits for a store still loading at startup, and checks for a keyless store
    before embedding anything.
    """
    if ctx.vector_store is None:
        _record_leg("vector", "absent")
        return []
    if not store_has_semantic_vectors(ctx.vector_store):
        _record_leg("vector", "keyless")
        return []
    ready = getattr(ctx, "vector_store_ready", None)
    if ready is not None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ready.wait(), timeout=30.0)
    vector = await question_vector(ctx, question)
    try:
        results = await asyncio.wait_for(
            vector_search(
                ctx.vector_store,
                question,
                _RETRIEVAL_FETCH_LIMIT + _DECISION_OVERFETCH,
                vector=vector,
            ),
            timeout=vector_search_timeout_s(),
        )
    except TimeoutError:
        _record_leg("vector", "timeout")
        _log.warning(
            "Vector search exceeded its %gs budget; answering without semantic hits. "
            "Raise it with %s=<seconds>.",
            vector_search_timeout_s(),
            _VECTOR_TIMEOUT_ENV,
        )
        return []
    except Exception:
        _record_leg("vector", "error")
        return []
    _record_leg("vector", "ok")
    return _pages_only(results, _RETRIEVAL_FETCH_LIMIT)


class _SymbolLegResult:
    """A file page from a name leg, in the shape FTS and vector results have."""

    __slots__ = ("page_id", "page_type", "snippet", "symbol_names", "title")

    def __init__(
        self, page_id: str, title: str, snippet: str, page_type: str, symbol_names: list[str]
    ) -> None:
        self.page_id = page_id
        self.title = title
        self.snippet = snippet
        self.page_type = page_type
        self.symbol_names = symbol_names


async def _safe_symbol_search(
    ctx: Any, question: str, *, pageless: bool = False
) -> list[_SymbolLegResult]:
    """File pages whose symbols the question's words name; [] on any failure.

    Runs on every question, not only identifier-shaped ones. ``pageless`` keeps
    files with no page for a caller that can serve them.
    """
    try:
        pages = await asyncio.wait_for(
            symbol_backed_pages(
                ctx,
                question,
                max_files=_SYMBOL_LEG_MAX_PAGES,
                symbol_limit=_SYMBOL_LEG_FETCH_LIMIT,
                pageless=pageless,
            ),
            timeout=5.0,
        )
    except TimeoutError:
        _record_leg("symbol", "timeout")
        _log.debug("get_answer symbol leg timed out; page retrieval stands")
        return []
    except Exception:
        _record_leg("symbol", "error")
        _log.debug("get_answer symbol leg failed; page retrieval stands", exc_info=True)
        return []
    _record_leg("symbol", "ok")
    return [
        _SymbolLegResult(
            p["page_id"],
            p["title"],
            (p.get("summary") or "")[:200],
            p["page_type"],
            p.get("symbol_names") or [],
        )
        for p in pages
    ]


async def _safe_filename_search(ctx: Any, question: str) -> list[tuple[_SymbolLegResult, bool]]:
    """Files the question names, each with whether it spells the whole name; [] on failure."""
    try:
        pages = await asyncio.wait_for(
            filename_backed_pages(ctx, question, max_files=_SYMBOL_LEG_MAX_PAGES), timeout=5.0
        )
    except TimeoutError:
        _record_leg("filename", "timeout")
        return []
    except Exception:
        _record_leg("filename", "error")
        _log.debug("filename leg failed; page retrieval stands", exc_info=True)
        return []
    _record_leg("filename", "ok")
    return [
        (
            _SymbolLegResult(p["page_id"], p["title"], p["summary"][:200], p["page_type"], []),
            p["full_cover"],
        )
        for p in pages
    ]


def leg_ranks(results: list[Any]) -> list[tuple[int, Any]]:
    """Each leg result with its rank among the leg's pages.

    A pageless file takes the rank of the page below it, so pages fuse as they
    would without it. Items may be results or ``(result, extra)`` pairs.
    """
    ranked: list[tuple[int, Any]] = []
    pages = 0
    for item in results:
        result = item[0] if isinstance(item, tuple) else item
        ranked.append((pages, item))
        if result.page_type != PAGELESS_FILE:
            pages += 1
    return ranked


def _hit_dict_from_result(result: Any) -> dict:
    """Convert a retriever result object to the pipeline's dict shape."""
    return {
        "page_id": result.page_id,
        "title": getattr(result, "title", ""),
        "snippet": getattr(result, "snippet", ""),
        "page_type": getattr(result, "page_type", ""),
        "score": 0.0,
        "_sources": set(),
    }


__all__ = [
    "_CONCEPT_FETCH_LIMIT",
    "_DECISION_OVERFETCH",
    "_DECISION_PREFIX",
    "_FILENAME_LEG_RRF_K",
    "_GRAPH_EXPAND_DAMPING",
    "_GRAPH_EXPAND_MAX_NEW",
    "_GRAPH_EXPAND_TOP_N",
    "_HUB_DEGREE_FLOOR",
    "_HUB_DEGREE_PERCENTILE",
    "_LEG_RECORD",
    "_PAGERANK_BIAS_MAX",
    "_PARENT_EXPAND_BOOST",
    "_PARENT_EXPAND_TOP_N",
    "_PARENT_MIN_SHARE",
    "_QUESTION_VECTORS",
    "_QUESTION_VECTOR_CACHE_MAX",
    "_RETRIEVAL_FETCH_LIMIT",
    "_RRF_K",
    "_RRF_SCORE_SCALE",
    "_SUBSYSTEM_QUERY_RE",
    "_SYMBOL_LEG_FETCH_LIMIT",
    "_SYMBOL_LEG_MAX_PAGES",
    "_SYMBOL_LEG_RRF_K",
    "_TEST_QUERY_RE",
    "_common_ancestor",
    "_hub_degree_cutoff",
    "_projected_edges",
    "_projected_pair",
    "_record_leg",
    "_safe_filename_search",
    "_safe_fts_search",
    "_safe_symbol_search",
    "_safe_vector_search",
    "_semantic_concept_paths",
    "apply_pagerank_bias",
    "begin_leg_record",
    "degraded_legs",
    "demote_noise_hits",
    "expand_via_graph",
    "expand_via_parent_page",
    "filename_backed_pages",
    "hybrid_retrieve",
    "hydrate_hits",
    "is_subsystem_query",
    "leg_ranks",
    "question_vector",
    "retrieval_legs",
    "stamp_hybrid_rank",
    "vector_search",
]
