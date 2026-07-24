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
import re
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
# Noise demotion (decision records + test file pages)
# ---------------------------------------------------------------------------

# Retrieval noise that should not occupy get_answer's top-5 on a plain question.
# Mirrors search_codebase's demotion (tool_search._sort_demoting_noise) on the
# answer pipeline's hit shape. Kept local so the answer pipeline does not import
# the search tool; the token lists are intentionally the same.
_TEST_PATH_TOKENS = ("/test/", "/tests/", "/__tests__/", "test_", "_test.", ".spec.", ".test.")
_TEST_QUERY_RE = re.compile(
    r"\b(test|tests|testing|tested|unit[\s-]?test|integration[\s-]?test|pytest|fixture|mock|spec)\b",
    re.IGNORECASE,
)


def _is_test_path(target_path: str) -> bool:
    tp = (target_path or "").lower()
    return any(tok in tp for tok in _TEST_PATH_TOKENS)


def demote_noise_hits(hits: list[dict], question: str, *, is_why: bool) -> list[dict]:
    """Stable-partition retrieval noise below real pages before the top-5 cap.

    get_answer applies no demotion of its own — decision records (short dense
    titles) and test file pages win RRF against the implementation a plain
    question is about and take top-5 slots that then feed synthesis. Decision
    records demote unless the question is why-shaped (decisions are the answer
    then, folded into the prelude); test file pages demote unless the question is
    explicitly about tests. Stable: real hits keep their order, and noise keeps
    its relative order at the tail (never dropped — an agent may still want it).
    """
    if not hits:
        return hits
    test_focused = bool(_TEST_QUERY_RE.search(question))

    def _is_noise(h: dict) -> bool:
        pt = h.get("page_type")
        return (pt == "decision_record" and not is_why) or (
            pt == "file_page"
            and not test_focused
            and _is_test_path(h.get("target_path", ""))
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
    for h in hits:
        meta = meta_by_id.get(h["page_id"], {})
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
    seed_paths = [h.get("target_path") for h in hits[:_GRAPH_EXPAND_TOP_N] if h.get("target_path")]
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
            vs.search(question, limit=_CONCEPT_FETCH_LIMIT), timeout=8.0
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
    top = [
        h
        for h in hits
        if h.get("target_path") and h.get("page_type") != "decision_record"
    ][:_PARENT_EXPAND_TOP_N]
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
