"""Human-readable relation labels and coupling strength for C4 edges.

The persisted graph stores raw edge-type tokens (``imports``,
``dynamic_imports``, ``co_changes``, ``calls`` …). Rolled up to box→box
relations these previously surfaced as opaque labels like ``"co_changes +1"``
plus a bare file-pair count. This module turns an aggregated edge's set of
types into one readable verb and buckets the pair-count into a qualitative
coupling notion, so the diagram reads in plain language instead of internals.

Pure functions — no DB, no I/O — so they unit-test in isolation.
"""

from __future__ import annotations

from collections.abc import Iterable

# Raw edge-type token -> the verb a reader expects on a C4 arrow.
_EDGE_VERB: dict[str, str] = {
    "imports": "imports",
    "dynamic_imports": "imports",
    "calls": "calls",
    "inherits": "inherits from",
    "implements": "implements",
    "references": "references",
    "contains": "contains",
    "co_changes": "co-changes",
}

# When an aggregated edge carries several types, a concrete code dependency is
# more informative than a historical (co-change) or containment signal, so we
# surface the single highest-priority verb rather than concatenating tokens.
_VERB_PRIORITY: tuple[str, ...] = (
    "calls",
    "inherits from",
    "implements",
    "imports",
    "references",
    "contains",
    "co-changes",
)

# Pair-count thresholds (inclusive lower bounds) for the coupling buckets.
_TIGHT_MIN = 50
_MODERATE_MIN = 10


def relation_label(edge_types: Iterable[str]) -> str:
    """Return the dominant human-readable verb for a set of raw edge types.

    Folds synonyms (``dynamic_imports`` -> ``imports``), de-duplicates, and
    picks the most informative verb by :data:`_VERB_PRIORITY`. Falls back to
    ``"depends on"`` for an empty or wholly-unknown set so an arrow is never
    unlabeled.
    """
    verbs = {_EDGE_VERB[t] for t in edge_types if t in _EDGE_VERB}
    for verb in _VERB_PRIORITY:
        if verb in verbs:
            return verb
    return "depends on"


def coupling_strength(edge_count: int) -> str:
    """Bucket a box→box pair-count into a qualitative coupling label.

    ``tight`` / ``moderate`` / ``loose`` reads as a meaningful strength where
    a raw count (``666``) does not.
    """
    if edge_count >= _TIGHT_MIN:
        return "tight"
    if edge_count >= _MODERATE_MIN:
        return "moderate"
    return "loose"
