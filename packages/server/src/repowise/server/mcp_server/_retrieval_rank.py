"""Shared intent-aware calibration for page retrieval legs."""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from collections.abc import Mapping

from repowise.server.mcp_server._query_terms import content_terms, split_humps

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

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


def _identifier_words(token: str) -> list[str]:
    """``generateModulePage`` / ``generate_module_page`` -> ``[generate, module, page]``."""
    return [w for w in re.split(r"[^a-z0-9]+", split_humps(token).lower()) if w]


def _typed_compounds(query: str, terms: set[str]) -> dict[str, list[str]]:
    """Ranking terms the query typed as one identifier, with their words.

    A typed compound (``module_page``, ``modulePage``) also counts inside a
    longer name (``generate_module_page``), which word-level tokens never produce.
    """
    compounds: dict[str, list[str]] = {}
    for raw in _IDENTIFIER_RE.findall(query):
        words = _identifier_words(raw)
        if len(words) >= 2 and raw.lower() in terms:
            compounds[raw.lower()] = words
    return compounds


def _names_compound(text: str, words: list[str]) -> bool:
    """Whether an identifier in *text* holds *words* as consecutive whole words.

    Prose never matches: ``module page`` is two identifiers, and ``this_test``
    does not hold ``is_test``.
    """
    n = len(words)
    for raw in _IDENTIFIER_RE.findall(text):
        parts = _identifier_words(raw)
        if any(parts[i : i + n] == words for i in range(len(parts) - n + 1)):
            return True
    return False


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
                # A page the symbol leg reached is about the names it matched.
                " ".join(hit.get("_symbol_names") or ()),
            ]
        )
        for hit in hits
    ]
    compounds = _typed_compounds(query, terms)
    tokens_by_hit = [
        _ranking_terms(text, max_terms=128)
        | {term for term, words in compounds.items() if _names_compound(text, words)}
        for text in texts_by_hit
    ]
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


# A query word naming a file lifts it by the word's squared rarity among indexed
# paths (1.0 when one path carries it), capped so content relevance still leads.
_PATH_NAME_LIFT_CAP = 1.0
# Below this many files nearly every word is rare, so a path match says nothing.
_MIN_INDEXED_PATHS = 30


def path_words(path: str) -> set[str]:
    """``services/_hotkey_pynput.py`` -> ``{services, hotkey, pynput}``; the
    extension is not a word."""
    return {w for w in _identifier_words(os.path.splitext(path)[0]) if len(w) >= 3}


def path_word_counts(paths: list[str]) -> Counter[str]:
    """How many of ``paths`` carry each path word."""
    return Counter(word for path in paths for word in path_words(path))


def boost_named_paths(
    hits: list[dict], query: str, df: Mapping[str, int], total: int, *, score_key: str
) -> None:
    """Lift hits whose path names a query word, by how rare that word is among
    the ``total`` indexed paths (``df`` per word). In place, unsorted."""
    if total < _MIN_INDEXED_PATHS:
        return
    log_total = math.log(total)
    weights = {
        t: (math.log(total / df[t]) / log_total) ** 2 for t in content_terms(query) if df.get(t)
    }
    if not any(weights.values()):
        return
    for hit in hits:
        path = hit.get("target_path") or ""
        score = hit.get(score_key)
        if not path or not score:
            continue
        words = path_words(path)
        lift = sum(w for t, w in weights.items() if t in words)
        if lift:
            hit[score_key] = round(score * (1.0 + min(_PATH_NAME_LIFT_CAP, lift)), 4)
