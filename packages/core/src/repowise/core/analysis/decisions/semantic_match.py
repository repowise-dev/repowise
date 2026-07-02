"""Embedding-based semantic matching for decision dedup (Phase 2C).

Title-string dedup (the cheap first pass in ``crud.bulk_upsert_decisions``)
collapses decisions whose normalized titles are identical, but it misses
paraphrases: *"Use Redis"* and *"Adopt Redis cache"* describe one decision yet
share no normalized title. This module adds the second pass on top of the
**page-generator vector store** — the same store wiki pages live in.

Decisions are embedded into that shared store under a ``decision:`` page-id
namespace with ``page_type="decision_record"``. Two consequences, both wanted:

- **Dedup reuses the store.** A residual (title-unmatched) candidate is matched
  by querying the store and keeping the nearest ``decision:`` neighbour whose
  cosine similarity clears a threshold — no separate decision index, no
  in-process re-embedding of the whole decision set.
- **Decisions become searchable.** Because they live in the same store as
  pages, ``search_codebase`` surfaces them alongside docs — a feature, not a
  leak: a query like "why Redis?" can return the decision record directly.

All backends return cosine similarity from ``search`` (InMemory/pgvector
natively; LanceDB normalised in ``lancedb_store``), so a single similarity
threshold applies uniformly.
"""

from __future__ import annotations

import contextlib
from typing import Any

__all__ = [
    "DECISION_PAGE_TYPE",
    "DECISION_VECTOR_PREFIX",
    "DEFAULT_DEDUP_TAU",
    "SEARCH_FETCH",
    "decision_match_text",
    "decision_vector_item",
    "find_duplicate_decision",
    "find_related_decisions",
    "find_related_decisions_many",
    "nearest_decision_hit",
    "upsert_decision_vector",
    "upsert_decision_vectors",
]

# Cosine threshold above which two decision texts are treated as the same
# decision. Tuned conservatively: high enough that unrelated decisions don't
# collapse, low enough that genuine paraphrases ("use Redis" / "adopt Redis
# cache") merge. Revisit empirically alongside the Phase-3 conflict detector,
# which reuses this similarity signal.
DEFAULT_DEDUP_TAU = 0.83

# Page-id namespace + page_type for decision vectors co-located in the page
# store. The prefix lets dedup filter store hits down to decisions; the
# page_type lets search surfaces label them.
DECISION_VECTOR_PREFIX = "decision:"
DECISION_PAGE_TYPE = "decision_record"

# How many nearest neighbours to pull before filtering to the decision
# namespace. Decisions are a small slice of a store dominated by page vectors,
# so we over-fetch to make sure a genuine paraphrase (which sits at very high
# similarity) isn't crowded out of the window by unrelated-but-near pages.
# Public: the batched dedup path in ``crud.bulk_upsert_decisions`` issues its
# own vector searches and must use the same window.
SEARCH_FETCH = 50
_SEARCH_FETCH = SEARCH_FETCH


def decision_match_text(title: str, decision: str = "") -> str:
    """The text used to embed a decision for semantic matching.

    Title carries the most signal; the decision statement disambiguates
    same-titled-but-different records. Kept in one place so the stored vector
    and the query side always embed the same shape.
    """
    title = (title or "").strip()
    decision = (decision or "").strip()
    return f"{title}\n{decision}".strip() if decision else title


def _decision_page_id(decision_id: str) -> str:
    return f"{DECISION_VECTOR_PREFIX}{decision_id}"


def decision_vector_item(
    decision_id: str,
    *,
    title: str,
    decision: str = "",
    evidence_file: str | None = None,
) -> tuple[str, str, dict] | None:
    """The ``(page_id, text, metadata)`` item that embeds a decision.

    Single source of truth for the store payload shared by the per-item
    :func:`upsert_decision_vector` and the batched write paths, so both always
    persist the same shape. Returns None for an empty decision text.
    """
    text = decision_match_text(title, decision)
    if not text:
        return None
    return (
        _decision_page_id(decision_id),
        text,
        {
            "title": title or "",
            "page_type": DECISION_PAGE_TYPE,
            "target_path": evidence_file or "",
            "content": text,
        },
    )


def nearest_decision_hit(
    results: list[Any],
    exclude_ids: set[str] | None = None,
) -> tuple[str, float] | None:
    """The nearest ``decision:``-namespace hit in best-first *results*.

    Shared filter for the text and vector search paths: returns
    ``(decision_id, score)`` of the first decision hit not in *exclude_ids*,
    or None when no decision appears in the window. Thresholding is the
    caller's job — the nearest hit decides, so a caller that finds it below
    its ``tau`` knows nothing closer exists.
    """
    exclude = exclude_ids or set()
    for r in results:
        page_id = getattr(r, "page_id", "")
        if not page_id.startswith(DECISION_VECTOR_PREFIX):
            continue
        decision_id = page_id[len(DECISION_VECTOR_PREFIX) :]
        if decision_id in exclude:
            continue
        return decision_id, float(getattr(r, "score", 0.0))
    return None


async def upsert_decision_vectors(store: Any, items: list[tuple[str, str, dict]]) -> None:
    """Batched, best-effort embed+upsert of decision items into the store.

    One :meth:`embed_texts` round instead of one embedder call per decision;
    falls back to :meth:`embed_batch` (still chunked, never per-item) when the
    store can't hand back raw vectors. Failures are swallowed like the
    per-item path: search visibility is an enhancement, never a reason to
    abort the SQL upsert that already happened.
    """
    if not items:
        return
    with contextlib.suppress(Exception):
        vectors = await store.embed_texts([text for _pid, text, _meta in items])
        if vectors is not None and len(vectors) == len(items):
            written = await store.upsert_vectors(
                [(pid, vec, meta) for (pid, _text, meta), vec in zip(items, vectors, strict=True)]
            )
            if written:
                return
        await store.embed_batch(items)


async def upsert_decision_vector(
    store: Any,
    decision_id: str,
    *,
    title: str,
    decision: str = "",
    evidence_file: str | None = None,
) -> None:
    """Embed a decision into the shared page store (idempotent on its id).

    Makes the decision both matchable for dedup and discoverable via
    ``search_codebase``. Failures are swallowed: a vector-store hiccup must
    never abort the SQL upsert that already happened.
    """
    item = decision_vector_item(
        decision_id, title=title, decision=decision, evidence_file=evidence_file
    )
    if item is None:
        return
    # Best-effort: search visibility / dedup is an enhancement, not a
    # correctness requirement for the SQL record, so a store hiccup is swallowed.
    with contextlib.suppress(Exception):
        await store.embed_and_upsert(*item)


async def find_duplicate_decision(
    store: Any,
    *,
    title: str,
    decision: str = "",
    tau: float = DEFAULT_DEDUP_TAU,
    exclude_ids: set[str] | None = None,
) -> str | None:
    """Return the id of an existing decision that is a semantic duplicate.

    Queries the shared store, filters hits to the ``decision:`` namespace
    (dropping any in *exclude_ids*), and returns the nearest one whose cosine
    similarity is ≥ *tau* — or None when nothing clears the bar. Store errors
    degrade to None (title dedup still applied upstream).
    """
    query = decision_match_text(title, decision)
    if not query:
        return None
    try:
        results = await store.search(query, limit=_SEARCH_FETCH)
    except Exception:
        return None

    # First (nearest) decision hit decides: if it clears the bar it's the
    # match; if not, nothing closer exists, so there is no duplicate.
    hit = nearest_decision_hit(results, exclude_ids)
    if hit is None:
        return None
    decision_id, score = hit
    return decision_id if score >= tau else None


async def find_related_decisions(
    store: Any,
    *,
    title: str,
    decision: str = "",
    lo: float,
    hi: float = 1.01,
    exclude_ids: set[str] | None = None,
    limit: int = 25,
) -> list[tuple[str, float]]:
    """Return ``(decision_id, similarity)`` for decisions *about the same topic*.

    Powers Phase-3 supersession/conflict detection. Where
    :func:`find_duplicate_decision` returns the single nearest neighbour above
    the dedup threshold (a *merge*), this returns every ``decision:`` neighbour
    whose cosine similarity sits in the band ``[lo, hi)`` — high enough to be
    the same topic, but (by default) *below* the dedup threshold so they were
    not collapsed into one record. Those are exactly the candidates that might
    contradict / supersede each other. Ordered best-first; store errors degrade
    to an empty list.
    """
    query = decision_match_text(title, decision)
    if not query:
        return []
    try:
        results = await store.search(query, limit=_SEARCH_FETCH)
    except Exception:
        return []
    return _related_from_results(results, lo=lo, hi=hi, exclude_ids=exclude_ids, limit=limit)


def _related_from_results(
    results: list[Any],
    *,
    lo: float,
    hi: float,
    exclude_ids: set[str] | None,
    limit: int,
) -> list[tuple[str, float]]:
    exclude = exclude_ids or set()
    out: list[tuple[str, float]] = []
    for r in results:
        page_id = getattr(r, "page_id", "")
        if not page_id.startswith(DECISION_VECTOR_PREFIX):
            continue
        decision_id = page_id[len(DECISION_VECTOR_PREFIX) :]
        if decision_id in exclude:
            continue
        score = getattr(r, "score", 0.0)
        if lo <= score < hi:
            out.append((decision_id, score))
        if len(out) >= limit:
            break
    return out


async def find_related_decisions_many(
    store: Any,
    items: list[tuple[str, str, set[str]]],
    *,
    lo: float,
    hi: float = 1.01,
    limit: int = 25,
) -> list[list[tuple[str, float]]]:
    """Batched :func:`find_related_decisions` — one embedding round, not N.

    *items* is ``(title, decision, exclude_ids)`` per query; the result list
    is aligned by index. Uses the store's ``search_many`` so every query is
    embedded in a single embedder call — the per-query network round-trip is
    what made the supersession pass scale with decision count. Store errors
    degrade to empty lists, matching the per-item helper.
    """
    if not items:
        return []
    queries = [decision_match_text(title, decision) for title, decision, _ in items]
    try:
        # Empty query texts still occupy their slot (keeps results aligned)
        # but must not reach the embedder — some providers reject "".
        all_results = await store.search_many([q or " " for q in queries], limit=_SEARCH_FETCH)
    except Exception:
        return [[] for _ in items]
    return [
        _related_from_results(results, lo=lo, hi=hi, exclude_ids=exclude, limit=limit)
        if query
        else []
        for results, query, (_t, _d, exclude) in zip(all_results, queries, items, strict=True)
    ]
