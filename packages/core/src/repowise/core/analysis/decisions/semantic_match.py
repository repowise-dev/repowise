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
    "PendingDecisionIndex",
    "decision_match_text",
    "decision_vector_item",
    "find_duplicate_decision",
    "find_duplicate_decision_by_vector",
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


def decision_match_text(title: str, decision: str = "") -> str:
    """The text used to embed a decision for semantic matching.

    Title carries the most signal; the decision statement disambiguates
    same-titled-but-different records. Kept in one place so the stored vector
    and the query side always embed the same shape.
    """
    title = (title or "").strip()
    decision = (decision or "").strip()
    if not decision:
        return title
    # When ``decision`` is just the title again (a body-less record whose title
    # was promoted into the decision field), don't emit the same line twice.
    if _same_line(title, decision):
        return title
    return f"{title}\n{decision}".strip()


def _same_line(a: str, b: str) -> bool:
    """True when two strings are the same line ignoring case and edge punctuation."""

    def norm(s: str) -> str:
        return s.strip().strip(".!?:;,").casefold()

    return norm(a) == norm(b)


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


class PendingDecisionIndex:
    """Nearest-neighbour index over decision vectors not yet written to the store.

    Backs the in-batch side of dedup in ``bulk_upsert_decisions``: records
    touched this batch are only matchable here until the deferred store write
    lands. Scoring uses numpy when available (it ships with every bundled
    persistent backend) so the per-group scan doesn't burn O(groups² · dim)
    pure-Python CPU on a large first index; without numpy it falls back to a
    norm-free dot loop over pre-normalised vectors.
    """

    def __init__(self) -> None:
        self._ids: list[str] = []
        self._id_set: set[str] = set()
        try:
            import numpy as np
        except ImportError:
            np = None  # type: ignore[assignment]
        self._np = np
        self._buf: Any = None  # numpy (capacity, dim) buffer of normalised rows
        self._vecs: list[list[float]] = []  # pure-Python fallback, normalised
        self._n = 0

    @property
    def ids(self) -> set[str]:
        return self._id_set

    def _normalise(self, vector: list[float]) -> Any:
        if self._np is not None:
            v = self._np.asarray(vector, dtype=self._np.float64)
            norm = float(self._np.linalg.norm(v))
            return v / norm if norm > 0 else None
        norm = sum(x * x for x in vector) ** 0.5
        return [x / norm for x in vector] if norm > 0 else None

    def add(self, decision_id: str, vector: list[float]) -> None:
        v = self._normalise(vector)
        if v is None:
            return
        if self._np is not None:
            if self._buf is None:
                self._buf = self._np.empty((64, len(vector)), dtype=self._np.float64)
            elif self._n == len(self._buf):
                grown = self._np.empty((len(self._buf) * 2, self._buf.shape[1]))
                grown[: self._n] = self._buf[: self._n]
                self._buf = grown
            self._buf[self._n] = v
        else:
            self._vecs.append(v)
        self._ids.append(decision_id)
        self._id_set.add(decision_id)
        self._n += 1

    def best(self, vector: list[float]) -> tuple[str, float] | None:
        """The ``(decision_id, cosine)`` of the nearest pending vector, or None."""
        if self._n == 0:
            return None
        q = self._normalise(vector)
        if q is None:
            return None
        if self._np is not None:
            scores = self._buf[: self._n] @ q
            i = int(scores.argmax())
            return self._ids[i], float(scores[i])
        best_i, best_score = 0, float("-inf")
        for i, v in enumerate(self._vecs):
            score = sum(x * y for x, y in zip(v, q, strict=True))
            if score > best_score:
                best_i, best_score = i, score
        return self._ids[best_i], best_score


async def find_duplicate_decision_by_vector(
    store: Any,
    vector: list[float],
    *,
    pending: PendingDecisionIndex | None = None,
    tau: float = DEFAULT_DEDUP_TAU,
    exclude_ids: set[str] | None = None,
) -> str | None:
    """Vector-side twin of :func:`find_duplicate_decision` for batched dedup.

    Searches the store by a precomputed query *vector* and, when *pending* is
    given, also scores against vectors touched earlier in the same batch that
    haven't been written to the store yet. Store hits whose ids appear in
    *pending* are skipped — the pending index holds a fresher vector for them
    than the store's stale row. Same policy as the text twin: the overall
    nearest decision decides, thresholded at *tau*; store errors degrade to
    the pending-only comparison.
    """
    best: tuple[str, float] | None = None
    try:
        results = await store.search_by_vector(vector, limit=SEARCH_FETCH)
    except Exception:
        results = None
    if results:
        exclude = set(exclude_ids or set())
        if pending is not None:
            exclude |= pending.ids
        best = nearest_decision_hit(results, exclude)
    if pending is not None:
        local = pending.best(vector)
        if local is not None and (best is None or local[1] > best[1]):
            best = local
    if best is None:
        return None
    decision_id, score = best
    return decision_id if score >= tau else None


async def upsert_decision_vectors(
    store: Any,
    items: list[tuple[str, str, dict]],
    *,
    vectors_by_text: dict[str, list[float]] | None = None,
) -> None:
    """Batched, best-effort embed+upsert of decision items into the store.

    One :meth:`embed_texts` round instead of one embedder call per decision,
    skipping texts whose vectors the caller already computed
    (*vectors_by_text* — the dedup pass embeds the same texts minutes
    earlier). Falls back to :meth:`embed_batch` when the store can't take raw
    vectors OR when the vector path raises, so a transient store error can't
    silently drop the whole batch. Failures of the fallback itself are
    swallowed like the per-item path: search visibility is an enhancement,
    never a reason to abort the SQL upsert that already happened.
    """
    if not items:
        return
    known = vectors_by_text or {}
    wrote = False
    try:
        to_embed = [(pid, text, meta) for pid, text, meta in items if text not in known]
        vectors = await store.embed_texts([text for _pid, text, _meta in to_embed])
        if vectors is not None and len(vectors) == len(to_embed):
            embedded = {
                text: vec for (_pid, text, _meta), vec in zip(to_embed, vectors, strict=True)
            }
            wrote = bool(
                await store.upsert_vectors(
                    [
                        (pid, known[text] if text in known else embedded[text], meta)
                        for pid, text, meta in items
                    ]
                )
            )
    except Exception:
        wrote = False
    if not wrote:
        with contextlib.suppress(Exception):
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
        results = await store.search(query, limit=SEARCH_FETCH)
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
        results = await store.search(query, limit=SEARCH_FETCH)
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
        all_results = await store.search_many([q or " " for q in queries], limit=SEARCH_FETCH)
    except Exception:
        return [[] for _ in items]
    if len(all_results) != len(items):
        # A store that drops or pads result slots would desync the zip below;
        # degrade the whole call to empty like any other store error.
        return [[] for _ in items]
    return [
        _related_from_results(results, lo=lo, hi=hi, exclude_ids=exclude, limit=limit)
        if query
        else []
        for results, query, (_t, _d, exclude) in zip(all_results, queries, items, strict=True)
    ]
