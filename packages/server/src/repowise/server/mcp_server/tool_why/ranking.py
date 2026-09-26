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

    Split out for workspace search, which ranks one store at a time and then has
    to merge the survivors. It is the *whole* key rather than the score because a
    merge that kept only the score would silently drop the other two rules: the
    occurrence-count tie-break and the status tie-break both exist because
    ``relevance`` is a share of one idf vector, so any two records covering the
    same set of question terms score bit-identically and something has to
    separate them. The floor and the ordering rules are documented on
    ``_rank_keyword_matches``; this returns the key that implements them.
    """
    terms = question_terms(query)
    texts = {id(d): _record_text(d) for d in all_decisions}
    idf = term_idf(terms, list(texts.values()))

    scored_decisions: list[tuple[float, float, int, Any]] = []
    for d in all_decisions:
        # Naming a file used to be a floor bypass: a record governing a target
        # scored 1.0 outright, on the reasoning that a caller who pointed at a
        # file instead of describing it left the record owing the question no
        # vocabulary. That reasoning holds for a call with *no* query, and this
        # function is never reached without one. With a query it meant the same
        # store, the same question and the same second refused without targets
        # and answered with them — measured on this repo, "why does
        # changed_lines() drop deletion-only files" came back led by "Move risk
        # scale reference metadata behind opt-in inclusion", whose only claim to
        # the question was appearing in the same directory.
        #
        # So every record is scored on what it says about the question. Naming a
        # target stays worth 5.0 in the ``_score_decision`` tie-break below, so a
        # governing record still outranks an equally relevant stranger; it just
        # no longer enters on a file path alone.
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

    Ranks by how much of the question's *vocabulary* a record carries, rarer
    words weighing more, with the older occurrence count breaking ties and
    status breaking the ties after that. Ordering by the occurrence count alone
    scored by length and by how ordinary a word is: measured here, "why do we
    use ruff check instead of ruff format" was won by records matching "use",
    "check", "format" and "instead" while the two ``active`` records that answer
    it — one of them 48 characters long — did not place at all.

    Status stays a tie-break and never a gate. Ordering by it *first* was tried
    and measured worse, and the reason generalises: only 69 of this repo's 614
    records are active, so a hard status gate serves three weakly-matching
    confirmed records ahead of the only relevant ones. The four records that
    answer "why is entry-point candidacy decided at ingestion" are all proposed,
    and status-first ordering made them unreachable.

    Records below the floor are dropped rather than ranked last, so an empty
    return is the honest answer and the caller turns it into a redirect. The
    surviving pool is wider than the serving cap because restatements are
    collapsed downstream, and a pool cut to the cap first would let three
    phrasings of one decision fill every slot.
    """
    return [d for _, d in _score_keyword_matches(all_decisions, query, target_set)]


def _evidence_key(d: Any) -> tuple[object, ...] | None:
    """The evidence a record cites, as a merge key, or ``None`` when it cites none.

    Re-extraction paraphrases a record's prose but not its provenance. The
    fourteen records on this repo that all restate one LIKE-escaping decision
    carry fourteen titles and fourteen ids and the same single
    ``evidence_commits`` entry; comment-sourced restatements repeat an
    ``evidence_file`` instead. Store-wide, 478 of 614 records sit in a cluster
    like that, which is why one query could return five phrasings of one
    decision.

    So the key is every cited full commit, or one exact source range, plus the
    extractor that read it, never the text. Normalising titles would need a
    tuned similarity threshold, and a wrong merge there hides a human-confirmed
    record. Incomplete commit hashes and file-only coordinates return ``None``:
    preserving an apparent duplicate is safer than erasing a real decision.

    This local decision-restatement key is intentionally narrower than the
    public evidence ids. A commit can support several real decisions, so shared
    public evidence never merges decision rows by itself.
    """
    return decision_collapse_key(d)


def _collapse_restatements(records: list) -> list[tuple[Any, list[str]]]:
    """``(kept, folded_ids)`` per distinct decision, input order preserved.

    Runs on records rather than on the projected dicts so the collapse happens
    *before* the lineage walk and the projection, neither of which should spend
    work on three phrasings of one decision. Records citing no evidence at all
    cannot be compared this way and are always kept.
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

    ``affected_files`` is deliberately absent. Joining a record's whole path
    list into one haystack and substring-matching a question against it scores
    by *breadth*: the record here governing 83 files matched almost every
    question asked, because ordinary query words ("index", "page", "format")
    occur somewhere in 83 paths, and it became the top hit for three of five
    probe questions including one about ruff. A scope is not question text.
    The legitimate use of that field — does this record govern the file the
    caller named — is the exact set membership in the target boost inside
    :func:`_score_decision`.
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

    Built from :func:`_weighted_fields` so a record cannot clear the floor on a
    field the tie-break cannot see, or the reverse.
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
        affected = set(json.loads(d.affected_files_json))
        affected_mods = json.loads(d.affected_modules_json)
        for t in target_files:
            if t in affected:
                score += 5.0  # Strong boost for exact file match
            elif any(t.startswith(m + "/") for m in affected_mods):
                score += 3.0  # Module-level match

    return score
