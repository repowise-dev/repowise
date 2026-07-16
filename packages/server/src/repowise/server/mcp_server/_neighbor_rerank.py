"""Retrieval-seeded neighborhood re-rank for flow questions.

A flow question — "how does X reach Y" — has its gold file in a subsystem the
answer traverses into, a hop or two from where retrieval lands. ``_flow_path.py``
rescues these only when the question *names* both endpoints; a behavioural flow
that names no file ("how does a changed file get its symbols re-persisted")
resolves no anchors and slips through, its gold clustered out of the top-5.

This stage seeds from the files retrieval already ranked, walks 1-2 hops out over
the same imports + projected-calls graph (skipping hub and plumbing nodes so a
busy file can't flood the frontier), and re-ranks the reached files by fusing
embedding and lexical relevance to the question. Within that ~200-file
neighbourhood a far endpoint that lost the corpus-wide retrieval wins, so it
rises; the top few contest the bottom served slot. The head is never reordered,
so a gold already surfaced can't be demoted.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.models import Page
from repowise.server.mcp_server._flow_path import _is_plumbing, _load_file_adjacency

# Motion cues that mark a data-flow question. Loose on purpose: the walk (reaches
# only files near the top hits) and the single contested slot are the real
# precision controls, so an over-firing gate costs compute, not correctness.
_FLOW_MOTION_CUES = (
    " into ",
    " feed",
    " travel",
    " become",
    " end up",
    "persist",
    " route ",
    " through ",
    " across ",
    " from ",
)
_MIN_TERMS = 4  # a flow names two things; below this it's a terse lookup

_SEED_TOP_N = 5  # top hits that seed the walk
_WALK_DEPTH = 2  # gold sits 1-2 hops from the seed cluster
_HUB_PERCENTILE = 0.95  # degree above which a node is a wall, not a frontier
_RANK_SCAN = 4000  # relevance re-scan depth; covers the corpus so no pool member is missed
_RRF_K = 60  # matches _answer_pipeline._RRF_K
_MAX_INJECT = 3  # reached files considered for injection
_KEEP_TOP = 4  # head never reordered; neighbours contest only the slot(s) below
_RANK_EPSILON = 1e-4  # keeps injected files in fused order under a shared score floor

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "of",
        "to",
        "in",
        "on",
        "for",
        "how",
        "does",
        "do",
        "what",
        "where",
        "when",
        "which",
        "that",
        "this",
        "its",
        "it",
        "get",
        "and",
        "or",
        "by",
        "into",
        "from",
        "with",
    }
)


def _terms(question: str) -> list[str]:
    raw = re.findall(r"[a-zA-Z0-9_]+", question.lower())
    return [t for t in raw if len(t) >= 3 and t not in _STOPWORDS]


def is_flow_question(question: str) -> bool:
    """True when the question looks like a data-flow between two subsystems."""
    if not question or len(_terms(question)) < _MIN_TERMS:
        return False
    q = " " + question.lower().strip() + " "
    return any(cue in q for cue in _FLOW_MOTION_CUES)


def _hub_cutoff(deg: dict[str, int]) -> int:
    """Nth-percentile degree — the wall for walking *through* a node."""
    if not deg:
        return 1 << 30
    vals = sorted(deg.values())
    return max(vals[min(len(vals) - 1, int(len(vals) * _HUB_PERCENTILE))], 1)


def _walk_neighborhood(adj: dict[str, set[str]], seeds: list[str]) -> set[str]:
    """Bounded BFS out from ``seeds``, never expanding through a hub or plumbing.

    Such nodes may be *reached* (a hub can be the gold) but are never expanded
    from, so a busy file can't drag its hundreds of neighbours into the pool.
    """
    deg = {n: len(v) for n, v in adj.items()}
    hub = _hub_cutoff(deg)
    seed_set = set(seeds)

    reached: dict[str, int] = {s: 0 for s in seeds if s in adj}
    frontier = list(reached)
    for depth in range(_WALK_DEPTH):
        nxt: list[str] = []
        for node in frontier:
            if depth > 0 and (deg.get(node, 0) > hub or _is_plumbing(node)):
                continue
            for nb in adj.get(node, ()):
                if nb not in reached:
                    reached[nb] = depth + 1
                    nxt.append(nb)
        frontier = nxt
        if not frontier:
            break
    return {n for n in reached if n not in seed_set and not _is_plumbing(n)}


async def _relevance_order(searcher: Any, question: str, pool: set[str]) -> list[str]:
    """Rank ``pool`` members by a store's relevance to ``question``.

    One corpus-wide search, kept in the store's returned order — order is all RRF
    consumes, so the two stores' score scales never need reconciling. Returns []
    on any store failure; the other arm then drives the fusion alone.
    """
    if searcher is None or not pool:
        return []
    try:
        results = await searcher.search(question, limit=_RANK_SCAN)
    except Exception:
        return []
    order, seen = [], set()
    for r in results:
        tp = getattr(r, "target_path", None)
        if tp in pool and tp not in seen:
            seen.add(tp)
            order.append(tp)
    return order


def _rrf_fuse(*orders: list[str]) -> dict[str, float]:
    """Reciprocal Rank Fusion of one or more ranked path lists."""
    fused: dict[str, float] = {}
    for order in orders:
        for rank, path in enumerate(order):
            fused[path] = fused.get(path, 0.0) + 1.0 / (_RRF_K + rank + 1)
    return fused


async def expand_via_neighbor_rerank(
    session: Any,
    repo_id: str,
    hits: list[dict],
    question: str,
    ctx: Any,
) -> list[dict]:
    """Surface the top hits' most relevant graph neighbours into ``hits``.

    No-op unless the question is flow-shaped and the bounded walk reaches files
    the fused relevance re-rank scores. The top ``_KEEP_TOP`` hits are kept in
    place; below them, buried originals and fabricated neighbours are re-ranked
    by the same fused relevance, so a strong far endpoint takes a served slot
    from a weak sibling while a protected gold is never demoted.
    """
    if not hits or not is_flow_question(question):
        return hits

    seed_paths = [h["target_path"] for h in hits[:_SEED_TOP_N] if h.get("target_path")]
    if not seed_paths:
        return hits

    adj = await _load_file_adjacency(session, repo_id)
    pool = _walk_neighborhood(adj, seed_paths) if adj else set()
    if not pool:
        return hits

    # Fuse embedding + lexical relevance over the pool AND the seeds (so buried
    # originals contest on the same signal), reusing the pipeline's two stores.
    scored = pool | set(seed_paths)
    vec_order = await _relevance_order(getattr(ctx, "vector_store", None), question, scored)
    fts_order = await _relevance_order(getattr(ctx, "fts", None), question, scored)
    fused = _rrf_fuse(vec_order, fts_order)
    if not fused:
        return hits

    hit_paths = {h.get("target_path") for h in hits}
    neighbours = sorted(pool, key=lambda p: fused.get(p, 0.0), reverse=True)[:_MAX_INJECT]
    absent = [p for p in neighbours if p not in hit_paths]

    meta_by_path: dict[str, tuple[str, str]] = {}
    if absent:
        res = await session.execute(
            select(Page.target_path, Page.summary, Page.page_type).where(
                Page.repository_id == repo_id,
                Page.target_path.in_(absent),
                Page.page_type == "file_page",
            )
        )
        meta_by_path = {
            tp: (summary or "", ptype or "file_page") for tp, summary, ptype in res.all()
        }

    floor = hits[min(_KEEP_TOP, len(hits)) - 1].get("score", 0.0)
    fabricated = [
        {
            "page_id": f"file_page:{path}",
            "target_path": path,
            "title": f"File: {path}",
            "summary": meta_by_path[path][0],
            "snippet": meta_by_path[path][0][:200],
            "page_type": meta_by_path[path][1],
            "score": floor - (i + 1) * _RANK_EPSILON,
            "_sources": {"neighbor_rerank"},
            "_expanded_from": "neighbor",
        }
        for i, path in enumerate(absent)
        if path in meta_by_path
    ]

    # Keep the head untouched so a gold already surfaced there survives; re-rank
    # everything below by fused relevance so a buried gold or a fabricated far
    # endpoint rises into the last served slot.
    rest = hits[_KEEP_TOP:] + fabricated
    rest.sort(key=lambda h: fused.get(h.get("target_path"), 0.0), reverse=True)
    return hits[:_KEEP_TOP] + rest
