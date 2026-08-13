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
#
# Every token the extractors actually emit must appear here. A missing one is
# silent: `relation_label` falls through to "depends on", which is the label
# reserved for a *wholly unknown* set, so a real, nameable dependency gets
# reported as the vaguest word we have. That is what happened to `framework`
# and `dynamic_uses`, which between them covered 1,351 of the 13,165 file-level
# edges on a live index (10%) and rendered as "depends on" for their whole life.
# `extends` is the token the extractors emit; `inherits` never was one.
# Total over EdgeType, and `test_edge_verb_covers_the_vocabulary` keeps it that
# way — the fall-through this comment describes is silent, so the only reliable
# fix is for a missing key to fail a test rather than render as "depends on".
# Dropped: "inherits", "references" and "contains", none of which is an edge
# type ("contains" and "tested_by" are knowledge-graph export labels).
_EDGE_VERB: dict[str, str] = {
    "imports": "imports",
    "dynamic_imports": "imports",
    "calls": "calls",
    "extends": "inherits from",
    "implements": "implements",
    "method_implements": "implements",
    # A lazy/registry import and a framework-convention link (a test file to its
    # conftest) are both real dependencies that no static import expresses.
    "dynamic_uses": "uses",
    "dynamic_url_route": "uses",
    "framework": "uses",
    "reads": "uses",
    # A type reference without an import: named, but not imported.
    "type_use": "references",
    # Containment. Only reachable when the view includes symbol nodes.
    "defines": "contains",
    "has_method": "contains",
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
    # Below "imports": where a pair has both, the static import is the more
    # precise description of the same dependency.
    "uses",
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
