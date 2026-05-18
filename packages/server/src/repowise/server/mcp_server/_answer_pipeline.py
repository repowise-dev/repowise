"""Retrieval pipeline for ``get_answer``.

This module owns everything that turns a developer question into a ranked
list of candidate wiki hits — but emphatically not the LLM step, the cache,
or the response shape (those live in ``tool_answer``). Separation of concerns
lets us iterate on retrieval quality without rereading the orchestrator and
vice versa.

Pipeline (each stage is a pure function over hit dicts):

    1. ``hybrid_retrieve``      — FTS + vector store in parallel, merged via
                                  Reciprocal Rank Fusion. Single retrieval
                                  modes systematically miss either token
                                  matches (vectors drift) or conceptual
                                  matches (FTS is literal). Two modes catch
                                  both classes of failure for the cost of one
                                  extra coroutine.
    2. ``hydrate_hits``         — attach target_path, summary, page_type from
                                  the Page table to each hit.
    3. ``apply_pagerank_bias``  — multiply scores by a damped PageRank factor
                                  so architecturally central files outrank
                                  peripheral ones at similar retrieval score.
                                  This is what rescues "how does X work"
                                  questions from peripheral consumers of X.
    4. ``expand_via_graph``     — for top-N hits, walk 1 hop through imports
                                  and pull in neighbors that have a wiki
                                  page. Rescues near-misses where retrieval
                                  landed in the right module but on a wrong
                                  file (consumer vs. orchestrator).

Stages downstream of this module (term coverage, intersection boost, domain
penalty) live in ``tool_answer`` for now — they're tightly coupled to the
existing question-aware symbol promotion code and not worth duplicating.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphEdge, GraphNode, Page

# How many candidates each retriever fetches before merging. Both modes
# tend to put the right answer in their top ~10, so 15 gives RRF room to
# resolve ties without dragging weak tail hits into the merge.
_RETRIEVAL_FETCH_LIMIT = 15

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


# ---------------------------------------------------------------------------
# Stage 1: Hybrid retrieval (FTS + vector → RRF merge)
# ---------------------------------------------------------------------------


async def hybrid_retrieve(question: str, ctx: Any) -> list[dict]:
    """Run FTS and vector retrieval in parallel and merge via RRF.

    Returns a list of dicts shaped ``{page_id, title, score, snippet,
    page_type, _sources: set[str]}``. ``_sources`` names which retrievers
    found the hit — useful for ranking signal ("hit by both modes" is a
    stronger ground-truth signal than "hit by one"). Score is the RRF-fused
    score; downstream stages may multiply it further.

    Both retrievers are best-effort with timeouts so one slow path can never
    block the call. An empty result from one mode just means the other mode
    fully drives ranking, which matches the pre-hybrid behaviour.
    """
    fts_task = _safe_fts_search(ctx, question)
    vec_task = _safe_vector_search(ctx, question)
    fts_results, vec_results = await asyncio.gather(fts_task, vec_task)

    # RRF merge. Each hit's contribution from a source is 1/(rank + k);
    # hits appearing in both sources sum their contributions naturally.
    fused: dict[str, dict] = {}
    for rank, h in enumerate(fts_results):
        entry = fused.setdefault(h.page_id, _hit_dict_from_result(h))
        entry["score"] = entry.get("score", 0.0) + 1.0 / (rank + _RRF_K)
        entry["_sources"].add("fts")
    for rank, h in enumerate(vec_results):
        entry = fused.setdefault(h.page_id, _hit_dict_from_result(h))
        entry["score"] = entry.get("score", 0.0) + 1.0 / (rank + _RRF_K)
        entry["_sources"].add("vector")

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
        return []
    try:
        return await asyncio.wait_for(
            ctx.fts.search(question, limit=_RETRIEVAL_FETCH_LIMIT), timeout=5.0
        )
    except Exception:
        return []


async def _safe_vector_search(ctx: Any, question: str) -> list[Any]:
    """Vector search wrapped in timeout + suppression. Returns [] on any failure.

    Also waits for vector-store readiness when the lifespan event is set —
    skipping the wait would race a background-loading store on cold start.
    """
    if ctx.vector_store is None:
        return []
    ready = getattr(ctx, "vector_store_ready", None)
    if ready is not None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ready.wait(), timeout=30.0)
    try:
        return await asyncio.wait_for(
            ctx.vector_store.search(question, limit=_RETRIEVAL_FETCH_LIMIT),
            timeout=8.0,
        )
    except Exception:
        return []


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
            select(Page.id, Page.target_path, Page.summary, Page.page_type).where(
                Page.id.in_(page_ids)
            )
        )
        meta_by_id = {
            row[0]: {
                "target_path": row[1] or "",
                "summary": row[2] or "",
                "page_type": row[3] or "",
            }
            for row in res.all()
        }

    out: list[dict] = []
    for h in hits:
        meta = meta_by_id.get(h["page_id"], {})
        target_path = meta.get("target_path", "")
        if scope and target_path and not target_path.startswith(scope):
            continue
        h["target_path"] = target_path
        h["summary"] = meta.get("summary", "")
        # Prefer the Page table's page_type when present — it's the source
        # of truth; retrievers sometimes carry stale or empty types.
        h["page_type"] = meta.get("page_type") or h.get("page_type", "")
        out.append(h)
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
    seed_paths = [
        h.get("target_path") for h in hits[:_GRAPH_EXPAND_TOP_N] if h.get("target_path")
    ]
    if not seed_paths:
        return hits
    existing = {h.get("target_path") for h in hits}

    async with get_session(ctx.session_factory) as session:
        # Importers (someone → seed) and importees (seed → someone) in one
        # query each. Two queries are fine — both hit the same indexed edge
        # table and run in <10ms on the corpus this is tuned for.
        importer_res = await session.execute(
            select(GraphEdge.source_node_id, GraphEdge.target_node_id).where(
                GraphEdge.target_node_id.in_(seed_paths),
            )
        )
        importee_res = await session.execute(
            select(GraphEdge.source_node_id, GraphEdge.target_node_id).where(
                GraphEdge.source_node_id.in_(seed_paths),
            )
        )

        neighbors: set[str] = set()
        for src, tgt in importer_res.all():
            if src and src not in existing:
                neighbors.add(src)
        for src, tgt in importee_res.all():
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

    if not page_rows:
        return hits

    # Damp parent score by _GRAPH_EXPAND_DAMPING for child candidates; pick
    # the strongest parent each child connects to (taking the max parent
    # score is conservative — favors well-connected neighbors).
    parent_score = max((h.get("score", 0.0) for h in hits[:_GRAPH_EXPAND_TOP_N]), default=0.0)
    candidates: list[dict] = []
    for path, summary, page_type in page_rows:
        candidates.append({
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
        })

    # Rank candidates by PageRank within the expansion set so we pick the
    # most central neighbor first when we have multiple plausible ones.
    candidates.sort(key=lambda c: -c.get("_pagerank", 0.0))
    additions = candidates[:_GRAPH_EXPAND_MAX_NEW]
    if not additions:
        return hits

    combined = hits + additions
    combined.sort(key=lambda h: h["score"], reverse=True)
    return combined
