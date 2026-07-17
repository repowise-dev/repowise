"""Repo-relative normalization of change-risk scores.

The raw :func:`change_risk.score_change` value is a calibrated absolute
probability, anchored to the offline calibration corpus — which makes its
high/moderate/low *banding* portable only across repos whose typical commit
resembles that corpus. On a repo whose median commit is large, the diff-size
feature (``la``) dominates and the absolute band skews high (two-thirds of
commits reading "high" was measured on this very repo). The *ranking* is sound;
the absolute band is not.

This module normalizes a score **against the repo's own distribution** so the
surface can show a portable, honest signal: where a commit sits relative to the
rest of the repo's history. The raw score stays the stored source of truth; we
re-bucket only for display.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass

# Tercile cut points on the repo-relative percentile (0-100). A commit in the
# top third of its repo's risk distribution is the review-priority tier.
_HIGH_PCT = 200.0 / 3.0  # ≈ 66.67
_MODERATE_PCT = 100.0 / 3.0  # ≈ 33.33
_PRIORITY_CLASSIFICATIONS = {
    "low": "Below typical",
    "moderate": "Typical",
    "high": "Elevated",
}


def review_priority_classification(priority: str | None) -> str | None:
    """Return the human-facing label for a repo-relative review priority."""
    return _PRIORITY_CLASSIFICATIONS.get(priority) if priority is not None else None


@dataclass
class RiskNormalizer:
    """Maps a raw change-risk score to its rank within one repo's distribution.

    Built once per request from the repo's full set of stored commit scores
    (bounded by the indexer's ``commit_limit``, so cheap to hold in memory).
    """

    scores: list[float]  # sorted ascending, ``None``/missing dropped

    @classmethod
    def from_scores(cls, scores: list[float | None]) -> RiskNormalizer:
        clean = sorted(float(s) for s in scores if s is not None)
        return cls(scores=clean)

    @property
    def count(self) -> int:
        return len(self.scores)

    def percentile(self, score: float | None) -> float:
        """Mid-rank percentile (0-100) of *score* within the repo distribution.

        Ties share the average rank, so identical scores get one percentile and
        the busiest repos don't pin every commit to 100. Returns 0.0 when there
        is no distribution to rank against.
        """
        n = len(self.scores)
        if n == 0 or score is None:
            return 0.0
        below = bisect_left(self.scores, score)
        equal = bisect_right(self.scores, score) - below
        return 100.0 * (below + 0.5 * equal) / n

    def priority(self, score: float | None) -> str:
        """Repo-relative review priority: ``high`` | ``moderate`` | ``low``.

        Derived from the repo-relative percentile (terciles), NOT the absolute
        calibrated band — so two-thirds of commits can never all read "high".
        """
        if score is None or not self.scores:
            return "low"
        pct = self.percentile(score)
        if pct >= _HIGH_PCT:
            return "high"
        if pct >= _MODERATE_PCT:
            return "moderate"
        return "low"
