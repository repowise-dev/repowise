"""The drift tiers mean what the dead-code tiers mean.

``doc_drift/constants.py`` copies dead code's bucket boundaries rather than
importing them, so that a change to dead code's *deletion-safety* policy cannot
silently retune a *display* tier in an unrelated detector. That decoupling is
deliberate, but it is only honest if someone notices when the two drift: a
reader comparing the two reports is entitled to assume "high" means one thing.

This test is that notice. If dead code moves its thresholds and drift should
follow, change both and update this file in the same commit. If they should
diverge, delete this test and say why in the comment above the constants.
"""

from __future__ import annotations

from repowise.core.analysis.dead_code.risk_factors import (
    RISK_CAP_CONFIDENCE,
    SAFE_CONFIDENCE_THRESHOLD,
)
from repowise.core.analysis.doc_drift.constants import (
    HIGH_CONFIDENCE_THRESHOLD,
    REVIEW_CONFIDENCE_THRESHOLD,
    bucket_confidences,
)


def test_high_boundary_matches_dead_code():
    assert HIGH_CONFIDENCE_THRESHOLD == SAFE_CONFIDENCE_THRESHOLD


def test_review_boundary_matches_dead_code():
    assert REVIEW_CONFIDENCE_THRESHOLD == RISK_CAP_CONFIDENCE


def test_buckets_are_exhaustive_and_disjoint():
    values = [1.0, 0.95, 0.7, 0.699, 0.5, 0.4, 0.399, 0.0]
    summary = bucket_confidences(values)
    assert sum(summary.values()) == len(values)
    assert set(summary) == {"high", "medium", "low"}


def test_boundaries_are_inclusive_at_the_bottom_of_each_tier():
    """A value exactly on a boundary belongs to the higher tier."""
    assert bucket_confidences([HIGH_CONFIDENCE_THRESHOLD])["high"] == 1
    assert bucket_confidences([REVIEW_CONFIDENCE_THRESHOLD])["medium"] == 1


def test_the_analyzer_and_the_read_path_bucket_identically():
    """The CRUD layer re-derives the summary on read; both call one function,
    and this pins that they keep agreeing."""
    from repowise.core.analysis.doc_drift import summarize_confidence
    from repowise.core.persistence.crud.analysis.doc_drift import (
        summarize_confidence_rows,
    )

    class _Row:
        def __init__(self, c: float) -> None:
            self.confidence = c

    values = [0.95, 0.9, 0.5, 0.2]
    rows = [_Row(v) for v in values]
    assert summarize_confidence(rows) == summarize_confidence_rows(rows)
