"""Vocabulary overlap between orientation pages.

Orientation pages (the repository overview, the architecture diagram, the
layer pages and the onboarding collection) are what a reader meets before any
reference content.  Nothing in generation checks whether two of them say the
same thing, so near-duplicates accumulate silently: they cost the reader time
and they give retrieval several adjacent-but-different pages to confuse for
each other.

This module measures that overlap.  Each page is reduced to its vocabulary —
the set of distinct word-ish tokens it uses — and every pair is scored by
Jaccard similarity (shared tokens divided by total distinct tokens).  It is
deliberately a vocabulary measure and not an embedding one: it needs no model,
it is stable across runs, and "these two pages draw on the same words" is
exactly the property that makes two orientation pages redundant to read.

Pairs are scored in two classes, because the two behave very differently:

* **Different page types** — an overview and an architecture diagram have no
  reason to share much vocabulary.  Real overlap here is a genuine duplicate.
* **Same page type** — eleven layer pages legitimately share a house style and
  a domain vocabulary, so they sit much higher as a matter of course.  Holding
  them to the cross-type threshold would flag almost every pair and bury the
  signal.

Both thresholds are configurable so they can be tuned without a code change.

The report distinguishes "no pairs were comparable" from "pairs were compared
and none overlapped".  Those are opposite facts — the first means the check
did not run on anything and its silence proves nothing — and collapsing them
into a single zero would let a broken orientation set read as a clean bill of
health.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from itertools import combinations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from .models import GeneratedPage

logger = logging.getLogger(__name__)

# The pages a reader meets before any reference content.
ORIENTATION_PAGE_TYPES: frozenset[str] = frozenset(
    {"onboarding", "layer_page", "repo_overview", "architecture_diagram"}
)

# Measured across the orientation set of a full index: pairs of *different*
# page types sit at or below 0.19, except one known duplicate at 0.24.  0.22
# separates them with margin on both sides.
DEFAULT_CROSS_TYPE_THRESHOLD = 0.22

# Pages of the *same* type share a template and a domain vocabulary, so they
# run far higher — the layer pages have a median near 0.38 with nothing wrong.
# 0.45 catches the genuinely interchangeable ones without flagging the rest.
DEFAULT_SAME_TYPE_THRESHOLD = 0.45

_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _vocabulary(content: str) -> frozenset[str]:
    """The set of distinct tokens in a page, lowercased."""
    return frozenset(_TOKEN_RE.findall(content.lower()))


def jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    """Shared tokens over total distinct tokens.  0.0 when both are empty."""
    a, b = frozenset(left), frozenset(right)
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


@dataclass(frozen=True)
class OverlapPair:
    """Two orientation pages and how much vocabulary they share."""

    page_id_a: str
    page_id_b: str
    title_a: str
    title_b: str
    page_type_a: str
    page_type_b: str
    similarity: float
    threshold: float

    @property
    def same_type(self) -> bool:
        return self.page_type_a == self.page_type_b

    def describe(self) -> str:
        return (
            f"{self.title_a!r} and {self.title_b!r} share "
            f"{self.similarity:.0%} of their vocabulary "
            f"(threshold {self.threshold:.0%})"
        )


@dataclass(frozen=True)
class OverlapReport:
    """Result of comparing the orientation pages in one generation run."""

    pages_compared: int = 0
    pairs_compared: int = 0
    pages_skipped_empty: int = 0
    flagged: tuple[OverlapPair, ...] = field(default_factory=tuple)
    cross_type_threshold: float = DEFAULT_CROSS_TYPE_THRESHOLD
    same_type_threshold: float = DEFAULT_SAME_TYPE_THRESHOLD

    @property
    def comparable(self) -> bool:
        """Whether the check actually compared anything.

        ``False`` means the run produced fewer than two orientation pages with
        content, so ``flagged`` being empty says nothing about duplication.
        Callers must not read an empty ``flagged`` as a pass without checking
        this first.
        """
        return self.pairs_compared > 0

    @property
    def flagged_count(self) -> int:
        return len(self.flagged)

    @property
    def highest_similarity(self) -> float | None:
        """The worst pair's score, or ``None`` if nothing was comparable."""
        if not self.pairs_compared:
            return None
        return max((p.similarity for p in self.flagged), default=0.0)

    def summary_line(self) -> str:
        """One line fit for a report table, honest about the not-run case."""
        if not self.comparable:
            return f"not computed ({self.pages_compared} orientation pages)"
        if not self.flagged:
            return f"no overlapping pairs ({self.pairs_compared} compared)"
        return f"{self.flagged_count} of {self.pairs_compared} pairs overlap"


def measure_orientation_overlap(
    pages: Sequence[GeneratedPage],
    *,
    cross_type_threshold: float = DEFAULT_CROSS_TYPE_THRESHOLD,
    same_type_threshold: float = DEFAULT_SAME_TYPE_THRESHOLD,
) -> OverlapReport:
    """Score vocabulary overlap across the orientation pages in ``pages``.

    Non-orientation pages are ignored.  Pages whose content has no tokens are
    counted in ``pages_skipped_empty`` rather than scored, because an empty
    page trivially shares nothing and would otherwise look like a clean result.

    Raises:
        ValueError: if either threshold is outside 0.0-1.0.  A nonsensical
            threshold would silently flag everything or nothing.
    """
    for name, value in (
        ("cross_type_threshold", cross_type_threshold),
        ("same_type_threshold", same_type_threshold),
    ):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be between 0.0 and 1.0, got {value!r}")

    scored: list[tuple[GeneratedPage, frozenset[str]]] = []
    skipped = 0
    for page in pages:
        if page.page_type not in ORIENTATION_PAGE_TYPES:
            continue
        vocab = _vocabulary(page.content or "")
        if not vocab:
            skipped += 1
            logger.warning(
                "Orientation page %r has no comparable content; "
                "excluding it from the overlap check",
                page.page_id,
            )
            continue
        scored.append((page, vocab))

    flagged: list[OverlapPair] = []
    pairs_compared = 0
    for (page_a, vocab_a), (page_b, vocab_b) in combinations(scored, 2):
        pairs_compared += 1
        threshold = (
            same_type_threshold if page_a.page_type == page_b.page_type else cross_type_threshold
        )
        similarity = jaccard(vocab_a, vocab_b)
        if similarity >= threshold:
            flagged.append(
                OverlapPair(
                    page_id_a=page_a.page_id,
                    page_id_b=page_b.page_id,
                    title_a=page_a.title,
                    title_b=page_b.title,
                    page_type_a=page_a.page_type,
                    page_type_b=page_b.page_type,
                    similarity=similarity,
                    threshold=threshold,
                )
            )

    flagged.sort(key=lambda p: (-p.similarity, p.page_id_a, p.page_id_b))

    report = OverlapReport(
        pages_compared=len(scored),
        pairs_compared=pairs_compared,
        pages_skipped_empty=skipped,
        flagged=tuple(flagged),
        cross_type_threshold=cross_type_threshold,
        same_type_threshold=same_type_threshold,
    )

    if not report.comparable:
        # Loud on purpose: a run that compared nothing must not be mistaken
        # for a run that found nothing.
        logger.warning(
            "Orientation overlap check compared no pairs (%d pages scored, "
            "%d skipped as empty); an empty result here is not a pass",
            report.pages_compared,
            skipped,
        )
    else:
        for pair in flagged:
            logger.warning("Orientation pages overlap: %s", pair.describe())

    return report
