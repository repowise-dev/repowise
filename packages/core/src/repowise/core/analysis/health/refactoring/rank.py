"""Compatibility facade for the canonical recommendation rank.

Analysis-time callers still ask for suggestions rather than serialized read
models.  The semantic owner is :mod:`.recommendations`; this module preserves
the old API while delegating benefit/cost/risk and deterministic ordering.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .models import RefactoringSuggestion
from .recommendations import build_recommendations


def score(s: RefactoringSuggestion, centrality: Mapping[str, float]) -> float:
    """Compatibility priority without persisted health/validation enrichment."""
    return build_recommendations([s], centrality=centrality)[0].rank_score


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
    recommendations = build_recommendations(suggestions, centrality=centrality or {})
    return [recommendation.suggestion for recommendation in recommendations]
