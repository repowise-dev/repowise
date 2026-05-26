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
    "decision_match_text",
    "find_duplicate_decision",
    "upsert_decision_vector",
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
_SEARCH_FETCH = 50


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
    text = decision_match_text(title, decision)
    if not text:
        return
    # Best-effort: search visibility / dedup is an enhancement, not a
    # correctness requirement for the SQL record, so a store hiccup is swallowed.
    with contextlib.suppress(Exception):
        await store.embed_and_upsert(
            _decision_page_id(decision_id),
            text,
            {
                "title": title or "",
                "page_type": DECISION_PAGE_TYPE,
                "target_path": evidence_file or "",
                "content": text,
            },
        )


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
    exclude = exclude_ids or set()
    try:
        results = await store.search(query, limit=_SEARCH_FETCH)
    except Exception:
        return None

    for r in results:  # results are ordered best-first
        page_id = getattr(r, "page_id", "")
        if not page_id.startswith(DECISION_VECTOR_PREFIX):
            continue
        decision_id = page_id[len(DECISION_VECTOR_PREFIX) :]
        if decision_id in exclude:
            continue
        # First (nearest) decision hit decides: if it clears the bar it's the
        # match; if not, nothing closer exists, so there is no duplicate.
        return decision_id if getattr(r, "score", 0.0) >= tau else None
    return None
