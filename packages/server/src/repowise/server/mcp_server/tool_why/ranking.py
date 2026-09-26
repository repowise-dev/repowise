"""Keyword relevance ranking and restatement collapse over decision records."""

from __future__ import annotations

import json
from typing import Any

from repowise.core.analysis.decisions.lifecycle import status_rank
from repowise.server.mcp_server._why_evidence import (
    decision_collapse_key,
)
from repowise.server.mcp_server._why_relevance import (
    clears_floor,
    question_terms,
    relevance,
    term_idf,
)


def _score_keyword_matches(
    all_decisions: list, query: str, target_set: set[str]
) -> list[tuple[tuple[float, float, int], Any]]:
    """``_rank_keyword_matches`` with each record's whole sort key kept beside it.

    For workspace search, which merges per-store survivors. The whole key, not
    the score: records covering the same question terms score identically, so a
    merge on score alone would drop both tie-breaks. The rules are documented on
    ``_rank_keyword_matches``.
    """
    terms = question_terms(query)
    texts = {id(d): _record_text(d) for d in all_decisions}
    idf = term_idf(terms, list(texts.values()))

    scored_decisions: list[tuple[float, float, int, Any]] = []
    for d in all_decisions:
        # Every record must clear the floor on what it says about the question;
        # naming a target only boosts the tie-break, never bypasses the floor.
        score = relevance(texts[id(d)], idf)
        if not clears_floor(score):
            continue
        scored_decisions.append(
            (
                -score,
                -_score_decision(d, set(terms), target_set),
                status_rank(d.status),
                d,
            )
        )
    scored_decisions.sort(key=lambda t: (t[0], t[1], t[2]))
    return [((t[0], t[1], t[2]), t[3]) for t in scored_decisions]


def _rank_keyword_matches(all_decisions: list, query: str, target_set: set[str]) -> list:
    """Records relevant enough to serve, best-first. Empty when none are.

    Ranks by how much of the question's vocabulary a record carries, rarer
    words weighing more; a raw occurrence count would reward length and common
    words. The occurrence count breaks ties, then status.

    Status is a tie-break, never a gate: few records are active, so a status
    gate would serve weak confirmed matches ahead of the relevant proposed ones.

    Records below the floor are dropped, so an empty return is an honest miss.
    The pool is wider than the serving cap because restatements collapse
    downstream.
    """
    return [d for _, d in _score_keyword_matches(all_decisions, query, target_set)]


def _evidence_key(d: Any) -> tuple[object, ...] | None:
    """The evidence a record cites, as a merge key, or ``None`` when it cites none.

    Re-extraction paraphrases a record's prose but not its provenance, so the
    key is the cited commits or exact source range plus the extractor, never
    the text: a wrong title merge could hide a confirmed record. Incomplete
    coordinates return ``None``, since keeping a duplicate beats erasing a
    decision. Narrower than public evidence ids, which several decisions share.
    """
    return decision_collapse_key(d)


def _collapse_restatements(records: list) -> list[tuple[Any, list[str]]]:
    """``(kept, folded_ids)`` per distinct decision, input order preserved.

    Runs on records, before the lineage walk and projection. Records citing no
    evidence cannot be compared and are always kept.
    """
    by_evidence: dict[tuple[object, ...], int] = {}
    out: list[tuple[Any, list[str]]] = []
    for d in records:
        key = _evidence_key(d)
        if key is not None and key in by_evidence:
            out[by_evidence[key]][1].append(d.id)
            continue
        if key is not None:
            by_evidence[key] = len(out)
        out.append((d, []))
    return out


def _weighted_fields(d: Any) -> list[tuple[float, str]]:
    """The searchable text of a record, lowercased, by field weight.

    ``affected_files`` is deliberately absent: matching words against paths
    scores a record by its breadth. A scope is not question text; it feeds the
    target boost in :func:`_score_decision` instead.
    """
    return [
        (3.0, d.title.lower()),
        (2.0, d.decision.lower()),
        (2.0, d.rationale.lower()),
        (1.5, d.context.lower()),
        (1.0, " ".join(json.loads(d.consequences_json)).lower()),
        (1.0, " ".join(json.loads(d.tags_json)).lower()),
        (1.0, (d.evidence_file or "").lower()),
    ]


def _record_text(d: Any) -> str:
    """The same text, unweighted, for term coverage and the relevance floor.

    Built from :func:`_weighted_fields` so the floor and tie-break see one text.
    """
    return " ".join(text for _, text in _weighted_fields(d))


def _score_decision(
    d: Any,
    query_words: set[str],
    target_files: set[str],
) -> float:
    """Score a decision against query words with field weighting and target boosting."""
    if not query_words:
        return 1.0 if target_files else 0.0

    score = 0.0
    for weight, text in _weighted_fields(d):
        for word in query_words:
            if word in text:
                score += weight

    # Target file boosting: decisions governing target files get a bonus
    if target_files:
        score += _target_boost(d, target_files)

    return score


def _target_boost(d: Any, target_files: set[str]) -> float:
    """Bonus for a record that governs the files the caller named."""
    boost = 0.0
    affected = set(json.loads(d.affected_files_json))
    affected_mods = json.loads(d.affected_modules_json)
    for t in target_files:
        if t in affected:
            boost += 5.0  # Strong boost for exact file match
        elif any(t.startswith(m + "/") for m in affected_mods):
            boost += 3.0  # Module-level match
    return boost
