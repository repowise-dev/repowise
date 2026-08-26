"""Shared intent-aware calibration for page retrieval legs."""

from __future__ import annotations

import math

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

    tokens_by_hit = [
        _ranking_terms(
                " ".join(
                    [
                        hit.get("title", "") or "",
                        hit.get("snippet", "") or "",
                        hit.get("summary", "") or "",
                        hit.get("target_path", "") or "",
                    ]
                ),
                max_terms=128,
            )
        for hit in hits
    ]
    document_frequency = {
        term: sum(1 for tokens in tokens_by_hit if term in tokens) for term in terms
    }
    weights = {
        term: 1.0 + math.log((len(hits) + 1) / (document_frequency[term] + 1))
        for term in terms
    }
    total_weight = sum(weights.values())

    for hit, tokens in zip(hits, tokens_by_hit, strict=True):
        coverage = sum(weight for term, weight in weights.items() if term in tokens) / total_weight
        raw = hit.get(score_key, 0.0) or 0.0
        hit["_coverage"] = coverage
        hit["_raw_score"] = raw
        hit[score_key] = raw * (floor + (1.0 - floor) * coverage)
    hits.sort(key=lambda hit: hit.get(score_key, 0.0), reverse=True)
    return hits
