"""Retrieval pipeline for ``get_answer``.

This module owns everything that turns a developer question into a ranked
list of candidate wiki hits — but emphatically not the LLM step, the cache,
or the response shape (those live in ``tool_answer``). Separation of concerns
lets us iterate on retrieval quality without rereading the orchestrator and
vice versa.

Pipeline (each stage is a pure function over hit dicts):

    1. ``hybrid_retrieve``      : FTS, vector store and the structural symbol
                                  index in parallel, merged via Reciprocal
                                  Rank Fusion. Single retrieval modes
                                  systematically miss either token matches
                                  (vectors drift) or conceptual matches (FTS
                                  is literal), and both page-shaped modes miss
                                  anything a generated file page does not
                                  spell out: its public-symbol table is all
                                  they can index, so a private helper or a
                                  local name is invisible to them. The symbol
                                  leg covers that third class, for the cost of
                                  one more coroutine.
    2. ``hydrate_hits``         — attach target_path, summary, page_type from
                                  the Page table to each hit.
    3. ``apply_pagerank_bias``  — multiply scores by a damped PageRank factor
                                  so architecturally central files outrank
                                  peripheral ones at similar retrieval score.
                                  This is what rescues "how does X work"
                                  questions from peripheral consumers of X.
    4. ``expand_via_graph``     — for top-N hits, walk 1 hop through the graph
                                  and pull in neighbors that have a wiki
                                  page. Rescues near-misses where retrieval
                                  landed in the right module but on a wrong
                                  file (consumer vs. orchestrator). Reference
                                  and co-change edges both qualify; see the
                                  note on the queries for why.

Stages downstream of this module (term coverage, intersection boost, domain
penalty) live in ``tool_answer`` for now — they're tightly coupled to the
existing question-aware symbol promotion code and not worth duplicating.

Stage modules: ``hydrate`` (2, 3), ``graph_expand`` (4), ``parent_page``,
``noise``, plus ``vectors`` and ``leg_record`` shared by the legs. The legs and
their merge stay here, so a leg replaced on this module is the one
``hybrid_retrieve`` calls.
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

# How many candidates each retriever fetches before merging. Both modes
# tend to put the right answer in their top ~10, so 15 gives RRF room to
# resolve ties without dragging weak tail hits into the merge.
_RETRIEVAL_FETCH_LIMIT = 15

# Page-id namespace decision vectors live under in the shared page store. Read
# from the module that writes them so the two cannot drift apart.
_DECISION_PREFIX = DECISION_VECTOR_PREFIX

# Decision records share the page vector store under this page-id namespace, so
# a page fetch has to ask for more rows than it needs and drop them. Over-fetch
# rather than post-filter a fixed window: a query whose nearest neighbours are
# all decisions would otherwise return a candidate set short by however many
# happened to rank high, which reads downstream as thin retrieval rather than as
# a filtered one. The margin is generous because decision rows cluster on the
# question-shaped text that also retrieves well.
_DECISION_OVERFETCH = 15

# RRF constant. The standard k=60 from the original RRF paper — large enough
# that rank-1 (1/61) and rank-2 (1/62) are close, small enough that rank-10
# (1/70) still contributes meaningfully when a hit only shows up in one mode.
_RRF_K = 60

# Multiplier applied to RRF scores so they land in roughly the same numeric
# range as the BM25 raw scores the downstream gates/thresholds (dominance
# ratio, high-confidence score floor, absolute-gap branch) were tuned for.
#
# Top-1 RRF with both modes hitting at rank 0 is 1/60 + 1/60 ≈ 0.033.
# Scaling by 180 puts it at ~6, matching the upper end of observed BM25
# scores on this corpus. Pure ordering preservation — never changes
# *which* hit ranks where, only the absolute numbers the gates compare.
_RRF_SCORE_SCALE = 180.0

# Third retrieval leg: the structural symbol index. FTS and the vector store
# both read the generated wiki page, which by construction carries an overview
# sentence, the *public* symbol table and dependency paths, with no function
# bodies, no private helpers, no local names. A question about something a page
# does not spell out therefore has nothing to match on, however well it is
# phrased. The symbol index does carry those names, and it is keyed on the
# words a symbol is built from, so it recovers exactly that class of miss.
#
# How many symbols the leg ranks, and how many distinct files they may
# contribute. Deliberately smaller than the other two legs' 15: this leg is
# there to add pool members the page legs cannot see, not to outvote them.
_SYMBOL_LEG_FETCH_LIMIT = 20
_SYMBOL_LEG_MAX_PAGES = 8

# The symbol leg's own RRF constant, larger than the page legs' so its
# contribution is smaller at every rank. Fusing it at k=60 like the others gave
# one lexical symbol match the same weight as a page two independent retrievers
# ranked first, which is not what a name match is worth: measured on the
# 99-question eval it pushed the correct ``distill/skeleton.py`` out of the
# served five in favour of a same-named React component. At k=180 the leg can
# lift a file the page retrievers already liked and can add one they never saw,
# but it cannot outvote them.
_SYMBOL_LEG_RRF_K = 180

# The filename leg is a name match too, weighted like the symbol leg. A name the
# question spells in full (``full_cover``, see ``_prose_symbols``) is fused at the
# page legs' k.
_FILENAME_LEG_RRF_K = _SYMBOL_LEG_RRF_K


# ---------------------------------------------------------------------------
# Stage 1: Hybrid retrieval (FTS + vector → RRF merge)
# ---------------------------------------------------------------------------


async def hybrid_retrieve(question: str, ctx: Any) -> list[dict]:
    """Run FTS, vector, symbol and filename retrieval in parallel and merge via RRF.

    Returns a list of dicts shaped ``{page_id, title, score, snippet,
    page_type, _sources: set[str]}``. ``_sources`` names which retrievers
    found the hit — useful for ranking signal ("hit by both modes" is a
    stronger ground-truth signal than "hit by one"). Score is the RRF-fused
    score; downstream stages may multiply it further.

    Both retrievers are best-effort with timeouts so one slow path can never
    block the call. An empty result from one mode just means the other mode
    fully drives ranking, which matches the pre-hybrid behaviour.

    The symbol and filename legs also name indexed files that have no page, as
    ``page_type: "file"`` hits, the same rows ``search_codebase`` serves.
    """
    # Reset before the legs run so the record describes this question and not
    # a previous one that happened to share the task context.
    begin_leg_record()
    fts_task = _safe_fts_search(ctx, question)
    vec_task = _safe_vector_search(ctx, question)
    sym_task = _safe_symbol_search(ctx, question, pageless=True)
    name_task = _safe_filename_search(ctx, question)
    fts_results, vec_results, sym_results, name_results = await asyncio.gather(
        fts_task, vec_task, sym_task, name_task
    )

    # RRF merge. Each hit's contribution from a source is 1/(rank + k);
    # hits appearing in both sources sum their contributions naturally.
    # Per-source rank is preserved alongside the fused score: RRF *compresses*
    # scores (rank-0-in-both barely outscores rank-1-in-both), so the summed
    # number loses the "both retrievers independently ranked this #1" signal.
    # Downstream confidence uses these ranks to recover retriever *agreement*
    # as a dominance signal the numeric ratio can't see.
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
    # Adds files the other legs missed and never reorders a page they found.
    # Search fuses the same rows into its own entry shape, crediting a symbol
    # page of the file; here every leg is keyed by page id, as above.
    for rank, (h, full) in leg_ranks(name_results):
        if h.page_id in fused and h.page_type != PAGELESS_FILE:
            continue
        entry = fused.setdefault(h.page_id, _hit_dict_from_result(h))
        entry["score"] = entry.get("score", 0.0) + 1.0 / (
            rank + (_RRF_K if full else _FILENAME_LEG_RRF_K)
        )
        entry["_sources"].add("filename")

    # Scale to BM25-range so downstream confidence/dominance gates (tuned
    # against the prior single-mode BM25 retrieval) keep behaving sanely.
    # Ordering is unchanged — multiplying by a positive constant is a
    # no-op for ranking.
    for entry in fused.values():
        entry["score"] = entry["score"] * _RRF_SCORE_SCALE

    merged = list(fused.values())
    merged.sort(key=lambda h: h["score"], reverse=True)
    return merged


def stamp_hybrid_rank(hits: list[dict]) -> None:
    """Record each hit's 0-based file rank in the fused order, before any rerank.

    This is the order ``search_codebase`` serves; confidence reads it to tell
    when the reranked lead left it. Symbol pages share their file's rank. Files
    are ranked among pages: a file with no page shares the rank of the page
    below it, so it never pushes a page down.
    """
    files: dict[str, int] = {}
    for h in hits:
        path = (h.get("target_path") or "").split("::", 1)[0]
        if h.get("page_type") == PAGELESS_FILE:
            h["_hybrid_rank"] = len(files)
        else:
            h["_hybrid_rank"] = files.setdefault(path, len(files))


async def _safe_fts_search(ctx: Any, question: str) -> list[Any]:
    """FTS search wrapped in timeout + suppression. Returns [] on any failure."""
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
    """Best-first *results* with decision vectors removed, capped at *limit*.

    Decision records live in the page store under their own page-id namespace so
    dedup can match a paraphrase and ``search_codebase`` can surface a decision
    directly. Neither is true of answering: a decision row has no ``wiki_pages``
    row, so hydration leaves it pathless — and a pathless hit skips the tombstone
    check and the scope filter, can only be reordered by noise demotion rather
    than dropped, and cannot be cited by the answer it helped write.

    A why-shaped question still gets its decisions. They are injected from a
    path-overlap query over ``decision_records``, which does not involve this
    store at all.
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
        # The over-fetch margin was not enough. Reported because the visible
        # symptom is a short candidate set, which otherwise reads as a corpus
        # too small or a query too narrow.
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
    """Vector search wrapped in timeout + suppression. Returns [] on any failure.

    Also waits for vector-store readiness when the lifespan event is set —
    skipping the wait would race a background-loading store on cold start.

    Returns nothing on a keyless index, before the readiness wait and before the
    question is embedded: there is no vector worth computing when there is
    nothing discriminative to compare it against. See
    ``store_has_semantic_vectors``. This leg is the only entry to vector
    retrieval here, so guarding it covers every caller.
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
    # Embedded here, before the store is asked anything: this is the first stage
    # to need the vector, and every later stage reads the same one back.
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
    """A file page reached through the symbol index, in retriever result shape.

    The RRF merge is keyed on page ids and reads four attributes off whatever
    the retrievers hand it, so the symbol leg presents its file pages the same
    way FTS and the vector store present theirs.
    """

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
    """File pages whose symbols the question's words name. [] on any failure.

    Ungated: it runs on every question, not only on ones that happen to carry
    an identifier-shaped token. Which indexes get read is not something the
    grammar of the sentence should decide. "How does an incremental update
    persist symbols" and ``_persist_symbols`` are after the same file, and
    before this leg only the second one reached the symbol index.

    Best-effort with a timeout, like the other two legs: a slow or missing
    symbol index degrades ``get_answer`` to its previous behaviour rather than
    failing the call. ``pageless`` keeps files that have no page, as pageless
    rows, for a caller that can serve them.
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
    """Files whose file name the question spells, each with whether it spells all
    of the name. [] on any failure, like the other legs."""
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
    """Each result of a leg with its rank among that leg's pages.

    A file with no page takes the rank of the page below it, so it never costs
    a page its place in the leg: the pages fuse exactly as they would without it.
    Items may be results or ``(result, extra)`` pairs.
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
