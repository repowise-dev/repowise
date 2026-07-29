"""Retrieval scoring: recall@k and mean reciprocal rank.

No model, no index, no network. Given the page ids a retrieval returned and the
page ids that should have been returned, these functions say how well it did.

Two rules the maths depends on, both enforced here rather than assumed:

* **Duplicate page ids are collapsed before ranking.** A page returned twice is
  one result at its first position. Without this a retrieval that repeats one
  page five times would fill the top-5 window and read as a legitimate miss.
* **A question with no expected pages is an error, not a zero.** Silently
  scoring it zero would let a malformed question set drag the average down with
  no sign that anything was wrong.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class ScoringError(ValueError):
    """A scoring input is malformed - bad k, no expected pages, no questions."""


@dataclass(frozen=True)
class RetrievalScores:
    """Aggregate scores over a whole question set."""

    n_questions: int
    recall_at_k: float
    mrr: float
    k: int
    n_empty_results: int
    """Questions whose retrieval returned nothing. These score zero; the count
    separates 'retrieval found the wrong pages' from 'retrieval found nothing'."""


def _rank_order(retrieved: Iterable[str]) -> list[str]:
    """Collapse duplicates, keeping each page id at its first position."""
    seen: set[str] = set()
    ordered: list[str] = []
    for page_id in retrieved:
        if page_id in seen:
            continue
        seen.add(page_id)
        ordered.append(page_id)
    return ordered


def _check_expected(expected: set[str]) -> None:
    if not expected:
        raise ScoringError("cannot score a question with no expected pages")


def recall_at_k(retrieved: Sequence[str], expected: set[str], k: int) -> float:
    """Fraction of the expected pages that appear in the first ``k`` results.

    Returns a value in [0, 1]. A result list shorter than ``k`` is not padded -
    two results containing the single expected page still score 1.0.
    """
    if k < 1:
        raise ScoringError(f"k must be >= 1, got {k}")
    _check_expected(expected)

    window = set(_rank_order(retrieved)[:k])
    return len(window & expected) / len(expected)


def reciprocal_rank(retrieved: Sequence[str], expected: set[str]) -> float:
    """``1 / rank`` of the first expected page, or 0.0 if none was retrieved."""
    _check_expected(expected)

    for position, page_id in enumerate(_rank_order(retrieved), start=1):
        if page_id in expected:
            return 1.0 / position
    return 0.0


def score_retrieval(
    results: Mapping[str, tuple[Sequence[str], set[str]]],
    k: int,
) -> RetrievalScores:
    """Aggregate per-question scores into a mean recall@k and MRR.

    ``results`` maps a question id to ``(retrieved page ids, expected page ids)``.
    """
    if not results:
        raise ScoringError("cannot score a run with no questions")

    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    n_empty = 0

    for question_id, (retrieved, expected) in results.items():
        if not retrieved:
            n_empty += 1
            logger.warning("retrieval returned no pages for question %s", question_id)
        recalls.append(recall_at_k(retrieved, expected, k=k))
        reciprocal_ranks.append(reciprocal_rank(retrieved, expected))

    return RetrievalScores(
        n_questions=len(results),
        recall_at_k=sum(recalls) / len(recalls),
        mrr=sum(reciprocal_ranks) / len(reciprocal_ranks),
        k=k,
        n_empty_results=n_empty,
    )
