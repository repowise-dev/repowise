"""Repo-relative change-risk normalization (RiskNormalizer)."""

from __future__ import annotations

from repowise.core.analysis.change_risk import RiskNormalizer, review_priority_classification


def test_empty_distribution_is_low_zero() -> None:
    n = RiskNormalizer.from_scores([])
    assert n.count == 0
    assert n.percentile(5.0) == 0.0
    assert n.priority(5.0) == "low"


def test_none_score_is_neutral() -> None:
    n = RiskNormalizer.from_scores([1.0, 2.0, 3.0])
    assert n.percentile(None) == 0.0
    assert n.priority(None) == "low"


def test_none_values_dropped_from_distribution() -> None:
    n = RiskNormalizer.from_scores([1.0, None, 3.0, None])
    assert n.count == 2


def test_midrank_percentile_orders_monotonically() -> None:
    n = RiskNormalizer.from_scores([0.0, 2.0, 4.0, 6.0, 8.0, 10.0])
    # The lowest score sits below the middle; the top score near the ceiling.
    assert n.percentile(0.0) < n.percentile(6.0) < n.percentile(10.0)
    assert n.percentile(10.0) > 90.0
    assert n.percentile(0.0) < 10.0


def test_ties_share_one_percentile() -> None:
    # Heavy clustering (the skew case): everything equal -> all mid-ranked at 50,
    # so the absolute "high" band can't fire on the whole repo.
    n = RiskNormalizer.from_scores([7.0] * 10)
    assert n.percentile(7.0) == 50.0
    assert n.priority(7.0) == "moderate"


def test_priority_terciles() -> None:
    n = RiskNormalizer.from_scores([float(i) for i in range(1, 10)])  # 1..9
    # Bottom third low, middle moderate, top third high.
    assert n.priority(1.0) == "low"
    assert n.priority(5.0) == "moderate"
    assert n.priority(9.0) == "high"


def test_priority_is_repo_relative_not_absolute() -> None:
    # A repo whose commits are ALL high-absolute-risk: the top still reads
    # "high" but the bottom reads "low" — the band is portable.
    n = RiskNormalizer.from_scores([8.0, 8.2, 8.4, 8.6, 8.8, 9.0])
    assert n.priority(8.0) == "low"
    assert n.priority(9.0) == "high"


def test_review_priority_classification() -> None:
    assert review_priority_classification("low") == "Below typical"
    assert review_priority_classification("moderate") == "Typical"
    assert review_priority_classification("high") == "Elevated"
    assert review_priority_classification(None) is None
