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
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import re
from collections import OrderedDict
from typing import Any

from sqlalchemy import func, select

from repowise.core.analysis.decisions.semantic_match import DECISION_VECTOR_PREFIX
from repowise.core.ingestion.models import CONTAINMENT_EDGE_TYPES
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphEdge, GraphNode, Page
from repowise.core.providers.embedding import store_has_semantic_vectors
from repowise.core.test_paths import is_test_path
from repowise.server.mcp_server._helpers import (
    _VECTOR_TIMEOUT_ENV,
    vector_search_timeout_s,
)
from repowise.server.mcp_server._prose_symbols import symbol_backed_pages

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

# Cap how many extra files graph expansion can add. Without a cap, a hub
# file (many importers) would flood the candidate set and dilute the LLM's
# context budget on tangential neighbors.
_GRAPH_EXPAND_TOP_N = 2
_GRAPH_EXPAND_MAX_NEW = 3

# Degree above which a neighbour is treated as a hub and dropped from the
# expansion set. A file that half the repo imports is a near-neighbour of
# everything, so it says nothing about *this* question; it is also exactly the
# file PageRank ranks first, which is why the ordering below needs a guard
# rather than trusting centrality on its own. Floor plus percentile, so a small
# repo where every file has a handful of edges excludes nothing.
_HUB_DEGREE_FLOOR = 50
_HUB_DEGREE_PERCENTILE = 0.99

# PageRank bias is multiplicative and capped. We don't want a marginally
# more central file to outrank a strong text match — only to break ties.
# Empirically PageRank values on this corpus span ~0 to ~0.01; we normalise
# to the max in the candidate set and scale to a [1.0, 1.3] multiplier.
_PAGERANK_BIAS_MAX = 0.3

# Damping factor for graph-expanded hits. They didn't surface in retrieval,
# so we trust them less than direct hits — but enough to outrank the bottom
# of the top-5 if the parent was strong. 0.7 keeps a strong parent's child
# (e.g. parent at 4.5, expanded child at 3.15) competitive with a real
# rank-3/4 hit (~3.0-3.5).
_GRAPH_EXPAND_DAMPING = 0.7

# Budget for embedding the question. The searches that used to embed inline were
# bounded at 8s including the embed, so the round-trip keeps that ceiling now
# that it happens on its own.
_EMBED_TIMEOUT_S = 8.0

# Which retrieval legs actually ran for the current question (finding A18).
#
# Every leg here is best-effort by design: a slow vector store must not be able
# to block an answer. The defect was that the fallback was *silent*. An embed
# that times out returns no vector, retrieval quietly continues lexical-only,
# and every health signal still reports green — ``embedder_live`` included,
# because a configured embedder is live whether or not this particular call
# beat the clock. Five queries in one bake-off run were answered without their
# vector leg and nothing in the response, the logs' structured fields, or the
# per-cell record said so.
#
# So the leg outcome travels with the answer. ``embedder_live`` says the
# embedder exists; this says whether it was used. Those are different claims
# and only one of them is about the answer the caller is holding.
_LEG_RECORD: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "repowise_retrieval_legs", default=None
)


def _record_leg(leg: str, outcome: str) -> None:
    """Note how one retrieval leg ended, if anyone is collecting."""
    record = _LEG_RECORD.get()
    if record is not None:
        record[leg] = outcome


def begin_leg_record() -> dict[str, str]:
    """Start collecting leg outcomes for one question. Returns the record.

    The legs run under ``asyncio.gather``, which copies the context into each
    task. Copying rebinds names, not objects, so the tasks all mutate this one
    dict and the caller sees what they wrote.
    """
    record: dict[str, str] = {}
    _LEG_RECORD.set(record)
    return record


def retrieval_legs() -> dict[str, str]:
    """How each retrieval leg ended for the question just answered.

    ``{"fts": "ok", "vector": "timeout", "symbol": "ok"}`` and so on, plus an
    ``embed`` key when embedding the question is what failed. Empty before any
    retrieval has run.
    """
    return dict(_LEG_RECORD.get() or {})


def degraded_legs(legs: dict[str, str]) -> list[str]:
    """The legs that *broke*, named. Empty when retrieval was whole.

    ``keyless`` is not degradation and is deliberately excluded. A keyless index
    has no semantic vectors by construction, so its vector leg is permanently
    absent rather than transiently broken, and naming it here would put a
    failure marker on every answer the mode ever produces. The configuration
    itself is reported once per response by ``_meta`` instead.
    """
    return sorted(leg for leg, outcome in legs.items() if outcome not in ("ok", "keyless"))

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


# ---------------------------------------------------------------------------
# The question's embedding, computed once per call
# ---------------------------------------------------------------------------

# Answering one question used to embed its text up to three times: once for the
# main fetch, once for the concept lookup on a subsystem-shaped question, once
# for the neighbourhood re-rank on a flow-shaped one. Each is a network
# round-trip to the embedding provider, billed, for a vector that cannot differ
# between them.
#
# Keyed by ``(id(store), question)`` and holding the store in the value, so an
# id can never be recycled onto a different store while its entries are live.
# Capacity is a handful of entries rather than one: concurrent calls with
# different questions would otherwise evict each other and re-embed. An entry is
# a pure function of (embedder, text), so a hit across calls is as correct as a
# hit within one.
_QUESTION_VECTOR_CACHE_MAX = 4
_QUESTION_VECTORS: OrderedDict[tuple[int, str], tuple[Any, list[float]]] = OrderedDict()


async def question_vector(ctx: Any, question: str) -> list[float] | None:
    """The question's embedding, computed at most once per store and question.

    Returns None when there is no store, the backend holds no embedder of its
    own, or the embed failed — callers then fall back to the store's own
    ``search(text)``, which embeds inline. That fallback is a cost regression,
    never a correctness one, so it warns rather than raising.
    """
    store = getattr(ctx, "vector_store", None)
    if store is None or not question:
        return None
    if not store_has_semantic_vectors(store):
        # Nothing will consume it, so do not pay to compute it.
        return None

    key = (id(store), question)
    cached = _QUESTION_VECTORS.get(key)
    if cached is not None:
        _QUESTION_VECTORS.move_to_end(key)
        return cached[1]

    try:
        vectors = await asyncio.wait_for(store.embed_texts([question]), timeout=_EMBED_TIMEOUT_S)
    except TimeoutError:
        # The A18 case, and the one worth naming separately: the embedder is
        # configured, reachable and healthy, and simply did not answer inside
        # the budget. Nothing downstream fails, so without this the answer is
        # lexical-only and indistinguishable from one that was not.
        _record_leg("embed", "timeout")
        _log.warning(
            "get_answer could not embed the question within %.1fs; retrieval "
            "continues without a question vector",
            _EMBED_TIMEOUT_S,
        )
        return None
    except Exception:
        _record_leg("embed", "error")
        _log.warning(
            "get_answer could not embed the question up front; each retrieval "
            "stage will embed it again",
            exc_info=True,
        )
        return None
    if not vectors:
        # Backend without an embedder of its own. Not an error — but it means
        # every stage pays for its own round-trip, so it is worth seeing.
        _log.debug("Vector store cannot embed directly; per-stage embedding stands")
        return None

    vector = [float(v) for v in vectors[0]]
    _QUESTION_VECTORS[key] = (store, vector)
    while len(_QUESTION_VECTORS) > _QUESTION_VECTOR_CACHE_MAX:
        _QUESTION_VECTORS.popitem(last=False)
    return vector


async def vector_search(
    store: Any, question: str, limit: int, *, vector: list[float] | None
) -> list[Any]:
    """Nearest pages to *question*, reusing *vector* when the backend allows it.

    Every backend that can search by raw vector is spared a second embedding of
    text it has already embedded; one that cannot returns None from
    ``search_by_vector`` and is asked to search the text as before.

    Returns nothing on a keyless store. Guarded here rather than only at the
    callers because this is the shared floor under every vector read in the
    answer pipeline, and the text fallback on the last line would otherwise
    route a keyless store straight back into the embedder this is avoiding.
    """
    if not store_has_semantic_vectors(store):
        return []
    if vector is not None:
        by_vector = await store.search_by_vector(vector, limit=limit)
        if by_vector is not None:
            return by_vector
    return await store.search(question, limit=limit)


# ---------------------------------------------------------------------------
# Stage 1: Hybrid retrieval (FTS + vector → RRF merge)
# ---------------------------------------------------------------------------


async def hybrid_retrieve(question: str, ctx: Any) -> list[dict]:
    """Run FTS, vector and symbol retrieval in parallel and merge via RRF.

    Returns a list of dicts shaped ``{page_id, title, score, snippet,
    page_type, _sources: set[str]}``. ``_sources`` names which retrievers
    found the hit — useful for ranking signal ("hit by both modes" is a
    stronger ground-truth signal than "hit by one"). Score is the RRF-fused
    score; downstream stages may multiply it further.

    Both retrievers are best-effort with timeouts so one slow path can never
    block the call. An empty result from one mode just means the other mode
    fully drives ranking, which matches the pre-hybrid behaviour.
    """
    # Reset before the legs run so the record describes this question and not
    # a previous one that happened to share the task context.
    begin_leg_record()
    fts_task = _safe_fts_search(ctx, question)
    vec_task = _safe_vector_search(ctx, question)
    sym_task = _safe_symbol_search(ctx, question)
    fts_results, vec_results, sym_results = await asyncio.gather(fts_task, vec_task, sym_task)

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
    for rank, h in enumerate(sym_results):
        entry = fused.setdefault(h.page_id, _hit_dict_from_result(h))
        entry["score"] = entry.get("score", 0.0) + 1.0 / (rank + _SYMBOL_LEG_RRF_K)
        entry["_sources"].add("symbol")
        entry["_sym_rank"] = rank

    # Scale to BM25-range so downstream confidence/dominance gates (tuned
    # against the prior single-mode BM25 retrieval) keep behaving sanely.
    # Ordering is unchanged — multiplying by a positive constant is a
    # no-op for ranking.
    for entry in fused.values():
        entry["score"] = entry["score"] * _RRF_SCORE_SCALE

    merged = list(fused.values())
    merged.sort(key=lambda h: h["score"], reverse=True)
    return merged


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

    __slots__ = ("page_id", "page_type", "snippet", "title")

    def __init__(self, page_id: str, title: str, snippet: str, page_type: str) -> None:
        self.page_id = page_id
        self.title = title
        self.snippet = snippet
        self.page_type = page_type


async def _safe_symbol_search(ctx: Any, question: str) -> list[_SymbolLegResult]:
    """File pages whose symbols the question's words name. [] on any failure.

    Ungated: it runs on every question, not only on ones that happen to carry
    an identifier-shaped token. Which indexes get read is not something the
    grammar of the sentence should decide. "How does an incremental update
    persist symbols" and ``_persist_symbols`` are after the same file, and
    before this leg only the second one reached the symbol index.

    Best-effort with a timeout, like the other two legs: a slow or missing
    symbol index degrades ``get_answer`` to its previous behaviour rather than
    failing the call.
    """
    try:
        pages = await asyncio.wait_for(
            symbol_backed_pages(
                ctx,
                question,
                max_files=_SYMBOL_LEG_MAX_PAGES,
                symbol_limit=_SYMBOL_LEG_FETCH_LIMIT,
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
        _SymbolLegResult(p["page_id"], p["title"], (p.get("summary") or "")[:200], p["page_type"])
        for p in pages
    ]


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


# ---------------------------------------------------------------------------
# Noise demotion (decision records + test file pages)
# ---------------------------------------------------------------------------

# Retrieval noise that should not occupy get_answer's top-5 on a plain question.
# Mirrors search_codebase's demotion (tool_search._sort_demoting_noise) on the
# answer pipeline's hit shape. Both now read the same test-path rules, so the
# two tools can no longer demote different sets of files.
_TEST_QUERY_RE = re.compile(
    r"\b(test|tests|testing|tested|unit[\s-]?test|integration[\s-]?test|pytest|fixture|mock|spec)\b",
    re.IGNORECASE,
)


def demote_noise_hits(hits: list[dict], question: str, *, is_why: bool) -> list[dict]:
    """Stable-partition retrieval noise below real pages before the top-5 cap.

    get_answer applies no demotion of its own — decision records (short dense
    titles) and test file pages win RRF against the implementation a plain
    question is about and take top-5 slots that then feed synthesis. Decision
    records demote unless the question is why-shaped (decisions are the answer
    then, folded into the prelude); test file pages demote unless the question is
    explicitly about tests. Stable: real hits keep their order, and noise keeps
    its relative order at the tail (never dropped — an agent may still want it).

    Tests demote, test support does not: "where are the shared fixtures" is a
    plain question whose answer is a ``conftest.py``, and demoting it would push
    the answer out of the window that feeds synthesis.
    """
    if not hits:
        return hits
    test_focused = bool(_TEST_QUERY_RE.search(question))

    def _is_noise(h: dict) -> bool:
        pt = h.get("page_type")
        return (pt == "decision_record" and not is_why) or (
            pt == "file_page" and not test_focused and is_test_path(h.get("target_path") or "")
        )

    real = [h for h in hits if not _is_noise(h)]
    noise = [h for h in hits if _is_noise(h)]
    return real + noise


# ---------------------------------------------------------------------------
# Stage 2: Hydrate hits with Page metadata (target_path, summary)
# ---------------------------------------------------------------------------


async def hydrate_hits(hits: list[dict], ctx: Any, *, scope: str | None = None) -> list[dict]:
    """Attach target_path, summary, and page_type from the Page table.

    Mutates each hit in place. Applies the ``scope`` filter (path prefix) at
    this stage rather than during retrieval — retrievers don't know about
    paths, and post-filtering keeps the merge logic source-agnostic.
    """
    if not hits:
        return hits
    page_ids = [h["page_id"] for h in hits]
    async with get_session(ctx.session_factory) as session:
        res = await session.execute(
            select(
                Page.id,
                Page.target_path,
                Page.summary,
                Page.page_type,
                Page.freshness_status,
            ).where(Page.id.in_(page_ids))
        )
        meta_by_id = {
            row[0]: {
                "target_path": row[1] or "",
                "summary": row[2] or "",
                "page_type": row[3] or "",
                "freshness": row[4] or "",
            }
            for row in res.all()
        }

    out: list[dict] = []
    pageless = 0
    for h in hits:
        meta = meta_by_id.get(h["page_id"])
        if meta is None:
            # A retrieved id with no page behind it: a decision vector, or a
            # vector left over from a page the stores have since disagreed
            # about. Either way there is nothing to cite and nothing to read,
            # and keeping it costs a served slot — while every later gate that
            # would have caught it (tombstone, scope) is keyed on a path it
            # does not have.
            pageless += 1
            continue
        # Tombstoned pages document deleted/renamed files — serving them as
        # answer material would cite code that no longer exists.
        if meta.get("freshness") == "tombstone":
            continue
        target_path = meta.get("target_path", "")
        if scope and target_path and not target_path.startswith(scope):
            continue
        h["target_path"] = target_path
        h["summary"] = meta.get("summary", "")
        # Prefer the Page table's page_type when present — it's the source
        # of truth; retrievers sometimes carry stale or empty types.
        h["page_type"] = meta.get("page_type") or h.get("page_type", "")
        out.append(h)
    if pageless:
        _log.warning(
            "get_answer dropped %d of %d retrieved hit(s) with no page row behind them; "
            "an id in a retriever that the page table does not know is either a "
            "non-page vector or a three-store disagreement (repowise doctor --repair)",
            pageless,
            len(hits),
        )
    return out


# ---------------------------------------------------------------------------
# Stage 3: PageRank bias
# ---------------------------------------------------------------------------


async def apply_pagerank_bias(hits: list[dict], ctx: Any) -> None:
    """Multiply each hit's score by a damped PageRank factor (in place).

    Looks up the GraphNode row for each hit's target_path and pulls its
    PageRank. We normalise within the candidate set rather than against the
    whole graph: a candidate with the highest PageRank among its peers gets
    the full bias, the lowest gets none. This avoids the failure mode where
    an absolute scale would over-reward famous-but-irrelevant files.
    """
    if not hits:
        return
    paths = [h.get("target_path") for h in hits if h.get("target_path")]
    if not paths:
        return
    async with get_session(ctx.session_factory) as session:
        # Look up GraphNodes by node_id — file nodes are keyed by their path.
        res = await session.execute(
            select(GraphNode.node_id, GraphNode.pagerank).where(
                GraphNode.node_id.in_(paths),
                GraphNode.node_type == "file",
            )
        )
        pr_by_path = {row[0]: float(row[1] or 0.0) for row in res.all()}

    if not pr_by_path:
        return
    max_pr = max(pr_by_path.values(), default=0.0)
    if max_pr <= 0:
        return

    for h in hits:
        pr = pr_by_path.get(h.get("target_path"), 0.0)
        # Normalised in [0, 1] then scaled to a multiplicative bias in
        # [1.0, 1 + _PAGERANK_BIAS_MAX].
        bias = 1.0 + _PAGERANK_BIAS_MAX * (pr / max_pr)
        h["_pagerank"] = pr
        h["_pagerank_bias"] = round(bias, 3)
        h["score"] = h.get("score", 0.0) * bias

    hits.sort(key=lambda h: h["score"], reverse=True)


# ---------------------------------------------------------------------------
# Stage 4: Graph expansion (1-hop neighbors of top hits)
# ---------------------------------------------------------------------------


async def _neighbor_degrees(session: Any, nodes: set[str]) -> dict[str, int]:
    """Total graph degree (in + out) for each node in *nodes*.

    One grouped count per direction over the same indexed edge table the
    expansion already reads, scoped to the candidate set rather than the whole
    graph.
    """
    if not nodes:
        return {}
    degree: dict[str, int] = {}
    for column in (GraphEdge.source_node_id, GraphEdge.target_node_id):
        res = await session.execute(
            select(column, func.count())
            .where(
                column.in_(nodes),
                # Count over the same edges the walk above traverses. Counting
                # containment too inflates a file's degree by the number of
                # symbols it declares, so a symbol-rich file reads as a hub on
                # its own size, and the p99 cutoff moves with how many symbol
                # nodes happen to be in the candidate set rather than with how
                # connected the files are.
                GraphEdge.edge_type.notin_(CONTAINMENT_EDGE_TYPES),
            )
            .group_by(column)
        )
        for node_id, count in res.all():
            degree[node_id] = degree.get(node_id, 0) + int(count or 0)
    return degree


def _hub_degree_cutoff(degree: dict[str, int]) -> int:
    """Degree at which a candidate counts as a hub, for this candidate set.

    ``max(floor, p99)``. The floor keeps a small or sparsely-linked repo from
    excluding ordinary files, and the percentile keeps a densely-linked one
    from excluding nothing.
    """
    if not degree:
        return 1 << 30
    values = sorted(degree.values())
    idx = min(len(values) - 1, int(len(values) * _HUB_DEGREE_PERCENTILE))
    return max(_HUB_DEGREE_FLOOR, values[idx])


async def expand_via_graph(hits: list[dict], ctx: Any) -> list[dict]:
    """Add up to ``_GRAPH_EXPAND_MAX_NEW`` graph-neighbor files to ``hits``.

    Rescues near-misses where the top retrieved file is in the right
    neighborhood but isn't the actual answer (a consumer instead of the
    orchestrator, a wrapper instead of the implementation). Expansion walks
    one hop through GraphEdges in both directions from the top-N candidates,
    then folds in any neighbor that:
      * has a wiki page (otherwise the LLM has nothing to read)
      * is not already in the candidate set

    Expanded hits carry an ``_expanded_from`` marker and a damped score so
    the gate / confidence calibration knows they're indirect.
    """
    if not hits:
        return hits
    seed_paths = [h.get("target_path") for h in hits[:_GRAPH_EXPAND_TOP_N] if h.get("target_path")]
    if not seed_paths:
        return hits
    existing = {h.get("target_path") for h in hits}

    async with get_session(ctx.session_factory) as session:
        # Inbound (someone → seed) and outbound (seed → someone) in one query
        # each. Two queries are fine — both hit the same indexed edge table and
        # run in <10ms on the corpus this is tuned for.
        #
        # Not import edges only, despite the naming this code used to carry:
        # ``co_changes`` neighbours stay in on purpose, because "this file
        # moves with yours" is a genuine read-this-too signal, and expansion
        # surfaces what it adds neutrally as ``[graph-expanded]`` rather than
        # as an import claim. Containment edges are excluded because they can
        # never contribute: their endpoint is a ``path::Name`` symbol node, and
        # the page lookup below matches ``target_path`` against ``file_page``
        # rows, so every such neighbour was fetched only to be discarded.
        neighbor_cols = (GraphEdge.source_node_id, GraphEdge.target_node_id)
        inbound_res = await session.execute(
            select(*neighbor_cols).where(
                GraphEdge.target_node_id.in_(seed_paths),
                GraphEdge.edge_type.notin_(CONTAINMENT_EDGE_TYPES),
            )
        )
        outbound_res = await session.execute(
            select(*neighbor_cols).where(
                GraphEdge.source_node_id.in_(seed_paths),
                GraphEdge.edge_type.notin_(CONTAINMENT_EDGE_TYPES),
            )
        )

        neighbors: set[str] = set()
        for src, _tgt in inbound_res.all():
            if src and src not in existing:
                neighbors.add(src)
        for _src, tgt in outbound_res.all():
            if tgt and tgt not in existing:
                neighbors.add(tgt)

        if not neighbors:
            return hits

        # Only fold in neighbors that have a wiki page — otherwise the LLM
        # context block can't carry a useful excerpt for them.
        page_res = await session.execute(
            select(Page.target_path, Page.summary, Page.page_type).where(
                Page.target_path.in_(neighbors),
                Page.page_type == "file_page",
            )
        )
        page_rows = list(page_res.all())

        # Also load PageRank for the neighbors so we can rank them.
        pr_res = await session.execute(
            select(GraphNode.node_id, GraphNode.pagerank).where(
                GraphNode.node_id.in_(neighbors),
                GraphNode.node_type == "file",
            )
        )
        pr_by_path = {row[0]: float(row[1] or 0.0) for row in pr_res.all()}
        degree = await _neighbor_degrees(session, neighbors)

    if not page_rows:
        return hits

    # Drop hubs before ranking. Ranking by PageRank alone actively prefers
    # them, which is the wrong instinct here: expansion is trying to name the
    # specific file the question is about, and the most-imported file in the
    # repo is the least specific candidate available.
    cutoff = _hub_degree_cutoff(degree)
    non_hub = [row for row in page_rows if degree.get(row[0], 0) <= cutoff]
    if non_hub:
        page_rows = non_hub

    # Damp parent score by _GRAPH_EXPAND_DAMPING for child candidates; pick
    # the strongest parent each child connects to (taking the max parent
    # score is conservative — favors well-connected neighbors).
    parent_score = max((h.get("score", 0.0) for h in hits[:_GRAPH_EXPAND_TOP_N]), default=0.0)
    candidates: list[dict] = []
    for path, summary, page_type in page_rows:
        candidates.append(
            {
                "page_id": f"file_page:{path}",
                "target_path": path,
                "title": f"File: {path}",
                "summary": summary or "",
                "snippet": (summary or "")[:200],
                "page_type": page_type or "file_page",
                "score": parent_score * _GRAPH_EXPAND_DAMPING,
                "_sources": {"graph_expand"},
                "_expanded_from": "graph",
                "_pagerank": pr_by_path.get(path, 0.0),
            }
        )

    # Rank candidates by PageRank within the expansion set so we pick the
    # most central neighbor first when we have multiple plausible ones.
    candidates.sort(key=lambda c: -c.get("_pagerank", 0.0))
    additions = candidates[:_GRAPH_EXPAND_MAX_NEW]
    if not additions:
        return hits

    combined = hits + additions
    combined.sort(key=lambda h: h["score"], reverse=True)
    return combined


# ---------------------------------------------------------------------------
# Stage: parent-concept surfacing (subsystem-shaped questions)
# ---------------------------------------------------------------------------

# A subsystem-shaped question asks about a part of the system as a whole:
# "overview of X", "main parts of X", "what subsystem does Y belong to",
# "where would I add a Z". For these the best answer is the concept page that
# documents the whole subsystem, not one of its member files or a more specific
# child concept page. Retrieval ranks the specific children above the parent (a
# child's embedding matches the query's noun more tightly than the broader
# parent), so the parent concept/rollup page never surfaces even though it
# exists. This gate is purely on the query's natural-language shape — no
# repo-specific vocabulary — so it generalises across codebases.
# Kept deliberately high-precision: bare fragments like "parts of", "belongs to",
# "structure of", or "which module" appear constantly inside ordinary how/where
# questions ("what parts of the code touch this", "which module imports config"),
# so each phrase is anchored to an unambiguous subsystem-overview intent. Better
# to miss an oddly-worded subsystem question (it just keeps today's ranking) than
# to reorder an implementation question's file hits.
_SUBSYSTEM_QUERY_RE = re.compile(
    r"\boverview of\b|\bgive me an overview\b|\bhigh[- ]level\b|"
    r"\bmain parts of\b|\bwhat (?:are|is) the (?:main )?(?:parts|pieces|components) of\b|"
    r"\barchitecture of\b|\bwalk me through\b|"
    r"\bhow (?:is|are|does) .+ (?:organi[sz]ed|structured|laid out|put together)\b|"
    r"\bwhat subsystem\b|\bwhich subsystem\b|"
    r"\bwhere would i (?:add|put)\b|\bwhere do i (?:add|put)\b",
    re.IGNORECASE,
)

# How many of the strongest real (non-noise) hits to cluster when looking for
# their shared subsystem. Decision records and pages without a path are skipped
# so a query whose top slots are crowded by decision noise still clusters on its
# real member files.
_PARENT_EXPAND_TOP_N = 8
# A directory is a tight structural cluster when it is the immediate parent of at
# least this many surfaced hits. A lone surfaced file is not enough structural
# signal on its own; that case is left to the semantic concept lookup.
_PARENT_MIN_SHARE = 2
# The injected parent leads the surface (it is the answer) but only just: a
# small multiplier over the current top score keeps it first without
# manufacturing a dominant retrieval that would inflate confidence to "high".
_PARENT_EXPAND_BOOST = 1.02
# How deep to look in a concept-restricted vector search for the subsystem page
# a query is semantically about. The window is wide because concept pages are a
# small minority of the corpus, so the right one can sit below many file/symbol
# hits before this filter drops those away.
_CONCEPT_FETCH_LIMIT = 60


def is_subsystem_query(question: str) -> bool:
    """True when the question asks about a subsystem/module as a whole, so the
    concept page for that subsystem should lead rather than its member files."""
    return bool(_SUBSYSTEM_QUERY_RE.search(question or ""))


def _common_ancestor(paths: set[str]) -> str:
    """Longest shared directory prefix of the given paths, segment-wise."""
    split = [p.split("/") for p in paths]
    common: list[str] = []
    for segs in zip(*split, strict=False):
        if len(set(segs)) == 1:
            common.append(segs[0])
        else:
            break
    return "/".join(common)


async def _semantic_concept_paths(question: str, ctx: Any) -> list[str]:
    """Target paths of the concept/layer pages a concept-restricted vector search
    ranks highest for the question, best-first.

    This is the "guaranteed concept-page candidate": when a subsystem query's
    file hits land in the wrong neighborhood (a UI consumer of the subsystem
    rather than the subsystem itself), the subsystem's own concept page still
    matches the query semantically and surfaces here even though it never
    entered the file-dominated main fetch. Best-effort: returns [] on any error
    so the caller degrades to the structural path.
    """
    vs = getattr(ctx, "vector_store", None)
    if vs is None:
        return []
    try:
        results = await asyncio.wait_for(
            vector_search(
                vs, question, _CONCEPT_FETCH_LIMIT, vector=await question_vector(ctx, question)
            ),
            timeout=vector_search_timeout_s(),
        )
    except Exception:
        return []
    out: list[str] = []
    for r in results:
        if getattr(r, "page_type", "") in ("module_page", "layer_page"):
            tp = getattr(r, "target_path", "") or ""
            if not tp:
                pid = getattr(r, "page_id", "") or ""
                tp = pid.split(":", 1)[1] if ":" in pid else pid
            if tp and tp not in out:
                out.append(tp)
    return out


async def expand_via_parent_page(hits: list[dict], question: str, ctx: Any) -> list[dict]:
    """Lead a subsystem-shaped question with the concept page for its subsystem.

    Two complementary signals pick that page, neither tuned to any repository:

    * Structural — when the surfaced file hits cluster under an ancestor
      directory that has a concept/rollup page, that ancestor IS the subsystem.
      Ancestors are found by walking each hit's target_path up the tree (the
      same relationship the generator uses to mint rollups). The tightest
      (deepest) ancestor covering >=2 hits wins, never a catch-all near the root.
    * Semantic — when the file hits land in the wrong neighborhood (a consumer
      of the subsystem, not the subsystem), the subsystem's own concept page
      still matches the query and is recovered by a concept-restricted vector
      search. Used only when no structural cluster is found, so a strong file
      cluster is never overridden by a semantic guess.

    The chosen page is promoted in place if already retrieved (its children
    out-embed it, so the cap drops it) or injected as the leading hit otherwise.
    A no-op on every non-subsystem question, so file/implementation queries keep
    today's ranking untouched.
    """
    if not hits or not is_subsystem_query(question):
        return hits
    # Cluster on the strongest real hits: skip decision records and any hit
    # without a path so decision noise crowding the top slots can't starve the
    # clustering of the member files that reveal the subsystem.
    top = [h for h in hits if h.get("target_path") and h.get("page_type") != "decision_record"][
        :_PARENT_EXPAND_TOP_N
    ]
    if not top:
        return hits

    # For each surfaced hit, count its immediate parent directory and credit
    # every strict ancestor with covering it. A dir that is the immediate parent
    # of two or more surfaced hits is a TIGHT cluster: the query's own member
    # files sit directly in it. Coverage by a distant ancestor (a broad root that
    # merely contains scattered hits) is deliberately not enough — that is what
    # separates a real subsystem from the repository root.
    imm_count: dict[str, int] = {}
    covers: dict[str, set[str]] = {}
    for h in top:
        tp = h["target_path"].rstrip("/")
        parent = tp.rsplit("/", 1)[0] if "/" in tp else ""
        if parent:
            imm_count[parent] = imm_count.get(parent, 0) + 1
        anc = parent
        while anc:
            covers.setdefault(anc, set()).add(tp)
            anc = anc.rsplit("/", 1)[0] if "/" in anc else ""

    tight_clusters = {d for d, c in imm_count.items() if c >= _PARENT_MIN_SHARE}
    semantic_paths = await _semantic_concept_paths(question, ctx)
    candidates = set(covers) | set(imm_count) | set(semantic_paths)
    if not candidates:
        return hits

    async with get_session(ctx.session_factory) as session:
        rows = (
            await session.execute(
                select(Page.target_path, Page.title, Page.summary, Page.page_type).where(
                    Page.target_path.in_(candidates),
                    Page.page_type.in_(("module_page", "layer_page")),
                )
            )
        ).all()
    if not rows:
        return hits
    by_path = {r[0]: r for r in rows}

    # A tight structural cluster is the query's own files agreeing on a subsystem,
    # so it outranks the semantic guess. When several sibling dirs each cluster
    # (a subsystem split into subdirectories), roll up to their common ancestor
    # page so the answer is the subsystem, not one arbitrary half of it. With no
    # tight cluster the file hits landed in the wrong neighborhood, so the concept
    # page the query is semantically about wins instead.
    tight_pages = tight_clusters & by_path.keys()
    winner = None
    if tight_pages:
        rollup = _common_ancestor(tight_pages)
        if len(tight_pages) > 1 and rollup in by_path and rollup in covers:
            winner = by_path[rollup]
        else:
            best_tp = max(tight_pages, key=lambda d: (imm_count[d], d.count("/")))
            winner = by_path[best_tp]
    else:
        # No tight cluster: the semantically-closest concept page (best-first),
        # then any weak cover ancestor as a last resort.
        winner = next((by_path[tp] for tp in semantic_paths if tp in by_path), None)
        if winner is None:
            cov_pages = [a for a in covers if a in by_path]
            if cov_pages:
                winner = by_path[max(cov_pages, key=lambda a: (a.count("/"), len(covers[a])))]
    if winner is None:
        return hits

    best_tp, best_title, best_summary, best_pt = winner
    top_score = max((h.get("score", 0.0) for h in hits), default=0.0) or 1.0
    lead_score = top_score * _PARENT_EXPAND_BOOST

    # Already in the candidate set but ranked below its own children (they embed
    # tighter to the query noun than the broader parent), so the top-5 cap drops
    # it. Promote it to lead in place rather than adding a duplicate. The bump is
    # small (just past the top hit) so it never manufactures a dominant retrieval
    # that would read "high".
    for h in hits:
        if h.get("target_path") == best_tp:
            if h.get("score", 0.0) < lead_score:
                h["score"] = lead_score
                src = h.get("_sources")
                if isinstance(src, set):
                    src.add("parent_promote")
            hits.sort(key=lambda x: x.get("score", 0.0), reverse=True)
            return hits

    # Not retrieved at all: inject it as the leading hit.
    parent_hit = {
        "page_id": f"{best_pt}:{best_tp}",
        "target_path": best_tp,
        "title": best_title or f"Overview: {best_tp}",
        "summary": best_summary or "",
        "snippet": (best_summary or "")[:200],
        "page_type": best_pt or "module_page",
        "score": lead_score,
        "_sources": {"parent_expand"},
        "_expanded_from": "parent",
    }
    combined = [parent_hit, *hits]
    combined.sort(key=lambda h: h["score"], reverse=True)
    return combined
