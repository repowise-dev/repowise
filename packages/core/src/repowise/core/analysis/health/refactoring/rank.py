"""Unified ranking across every refactoring detector.

Each detector sorts its own output deterministically, but the surfaces (CLI,
MCP, web) present a single mixed list — so the *global* order is what the user
actually sees. This module imposes one ranking over all types so the top of
the list is always the most valuable refactoring regardless of kind, instead
of a churn-only or impact-only sort that buries cross-file wins.

The key blends three orthogonal signals as a product of ``(1 + signal)``
factors (so a zero in any one dimension shapes the order without annihilating
the suggestion):

- **recovered impact** — the health the refactoring buys back (0 for the
  graph-native types that answer no biomarker);
- **target centrality** — how depended-upon the file is, so a refactoring on a
  hub file outranks the same refactoring on a leaf;
- **blast radius** — how much else moves, as a mild amplifier (a wide, real
  refactoring is worth surfacing) rather than a penalty;
- **confidence** — a final weight so a high-confidence plan edges out a
  borderline one at the same score.

Because impact-free types still rank via centrality and blast, Move Method and
Break Cycle interleave fairly with Extract Class / Extract Helper rather than
sinking below every impact-bearing suggestion.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from .models import RefactoringSuggestion

_CONFIDENCE_WEIGHT = {"high": 1.25, "medium": 1.0, "low": 0.75}


def _blast_size(s: RefactoringSuggestion) -> int:
    """A single 'how much else moves' integer, read from whichever blast-radius
    shape this refactoring type carries."""
    br = s.blast_radius or {}
    for key in ("file_count", "dependents_count", "callers"):
        value = br.get(key)
        if isinstance(value, (int, float)) and value:
            return int(value)
    files = br.get("files")
    return len(files) if isinstance(files, list) else 0


def _enrich_blast_radius(s: RefactoringSuggestion, centrality: Mapping[str, float]) -> None:
    """Give every plan a caller/dependents rollup so the 'what else moves'
    signal is uniform across types. Extract Helper carries only its
    co-occurrence files; here we add the importing-file count summed over the
    occurrences (the callers that ride along), which the ranking then reads."""
    br = dict(s.blast_radius or {})
    if "callers" not in br and "dependents_count" not in br:
        files = br.get("files")
        if isinstance(files, list) and files:
            br["callers"] = sum(int(centrality.get(f, 0)) for f in files)
    s.blast_radius = br


def score(s: RefactoringSuggestion, centrality: Mapping[str, float]) -> float:
    """The unified rank score (higher = surface sooner)."""
    impact_factor = 1.0 + max(0.0, float(s.impact_delta or 0.0))
    cen = float(centrality.get(s.file_path, 0.0))
    centrality_factor = 1.0 + math.log1p(max(0.0, cen))
    blast_factor = 1.0 + math.log1p(_blast_size(s))
    confidence_weight = _CONFIDENCE_WEIGHT.get(s.confidence, 1.0)
    return impact_factor * centrality_factor * blast_factor * confidence_weight


def rank_suggestions(
    suggestions: Sequence[RefactoringSuggestion],
    *,
    centrality: Mapping[str, float] | None = None,
) -> list[RefactoringSuggestion]:
    """Return *suggestions* in unified-rank order (a new list).

    *centrality* maps a file path to its dependency centrality (the importer
    count / in-degree is a good cheap proxy); missing files score 0. Ties break
    on type, file, then target so the order is fully deterministic. Also
    enriches each suggestion's blast radius in place so the persisted/rendered
    plan carries the same caller rollup the ranking used.
    """
    cen = centrality or {}
    for s in suggestions:
        _enrich_blast_radius(s, cen)
    return sorted(
        suggestions,
        key=lambda s: (
            -score(s, cen),
            s.refactoring_type,
            s.file_path,
            s.target_symbol,
        ),
    )
