"""Shared intent-aware calibration for page retrieval legs."""

from __future__ import annotations

import math
import re

from repowise.server.mcp_server._query_terms import content_terms

_INFLECTIONS = {
    "changed": "change",
    "changes": "change",
    "files": "file",
    "tested": "test",
    "testing": "test",
    "tests": "test",
}


def _ranking_terms(text: str, *, max_terms: int = 8) -> set[str]:
    """Content terms with a tiny retrieval-only inflection normalisation."""
    return {_INFLECTIONS.get(term, term) for term in content_terms(text, max_terms=max_terms)}


def rerank_by_context_coverage(
    hits: list[dict],
    query: str,
    *,
    score_key: str,
    floor: float,
    absolute_stopwords: set[str] | None = None,
) -> list[dict]:
    """Calibrate a fused page window with discriminating query-term coverage.

    The input score remains authoritative. Coverage is a bounded multiplier,
    computed over title, prose, and path/module identity. Candidate-relative
    term weights make vocabulary repeated by the whole window weak evidence,
    while terms that distinguish one candidate carry more weight.
    """
    terms = _ranking_terms(query)
    if not terms or not hits:
        return hits

    texts_by_hit = [
        " ".join(
            [
                hit.get("title", "") or "",
                hit.get("snippet", "") or "",
                hit.get("summary", "") or "",
                hit.get("target_path", "") or "",
            ]
        )
        for hit in hits
    ]
    tokens_by_hit = [_ranking_terms(text, max_terms=128) for text in texts_by_hit]
    document_frequency = {
        term: sum(1 for tokens in tokens_by_hit if term in tokens) for term in terms
    }
    weights = {
        term: 1.0 + math.log((len(hits) + 1) / (document_frequency[term] + 1))
        for term in terms
    }
    total_weight = sum(weights.values())

    coverages = [
        sum(weight for term, weight in weights.items() if term in tokens) / total_weight
        for tokens in tokens_by_hit
    ]
    best_coverage = max(coverages)

    legacy_terms = (
        [
            term
            for term in re.findall(r"[a-zA-Z0-9_]+", query.lower())
            if len(term) >= 3 and term not in absolute_stopwords
        ]
        if absolute_stopwords is not None
        else []
    )

    for hit, text_value, tokens, coverage in zip(
        hits, texts_by_hit, tokens_by_hit, coverages, strict=True
    ):
        raw = hit.get(score_key, 0.0) or 0.0
        hit["_coverage"] = coverage
        hit["_raw_score"] = raw
        # Ranking is candidate-relative so the best contextual match keeps its
        # source score and weaker matches lose ground. Confidence, however,
        # keeps get_answer's pre-existing absolute coverage scale: relative
        # calibration may change order, but cannot manufacture dominance.
        if legacy_terms:
            haystack = text_value.lower()
            absolute_coverage = sum(term in haystack for term in legacy_terms) / len(
                legacy_terms
            )
        else:
            absolute_coverage = len(terms & tokens) / len(terms)
        absolute_multiplier = floor + (1.0 - floor) * max(
            coverage, absolute_coverage
        )
        relative_coverage = coverage / best_coverage if best_coverage else 1.0
        ranking_multiplier = floor + (1.0 - floor) * relative_coverage
        hit["_coverage_multiplier"] = ranking_multiplier
        hit["_confidence_score_factor"] = absolute_multiplier / ranking_multiplier
        hit[score_key] = raw * ranking_multiplier
    hits.sort(key=lambda hit: hit.get(score_key, 0.0), reverse=True)
    return hits
