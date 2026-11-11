"""Ranking helpers shared by the refactoring and performance opportunity orders."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.analysis.health.perf import opportunity_rank as perf_rank
from repowise.core.analysis.health.rank_common import top_factors, weakest
from repowise.core.analysis.health.refactoring import opportunity_rank as refactoring_rank


def test_weakest_is_lowest_strength_then_name() -> None:
    strength = {"direct": 3, "call-site": 3, "reliable-edge": 2}
    assert weakest({"direct", "reliable-edge"}, strength) == "reliable-edge"
    assert weakest({"direct", "call-site"}, strength) == "call-site"


def test_weakest_counts_an_unlisted_label_as_zero() -> None:
    assert weakest(["direct", "mystery"], {"direct": 3}) == "mystery"


def test_top_factors_drops_zeros_and_orders_by_magnitude_then_name() -> None:
    factors = {"b": 2.0, "a": 2.0, "zero": 0.0, "neg": -5.0, "small": 0.5}
    assert top_factors(factors, 3) == [("neg", -5.0), ("a", 2.0), ("b", 2.0)]
    assert top_factors({"zero": 0}, 3) == []


def test_refactoring_rank_keeps_its_shape() -> None:
    steps = [SimpleNamespace(confidence="high"), SimpleNamespace(confidence="medium")]
    assert refactoring_rank.weakest_confidence(steps) == "medium"
    assert refactoring_rank.why_ranked({"cost": 2.0, "risk": 0.0, "benefit": 3.0}) == [
        {"factor": "benefit", "value": 3.0},
        {"factor": "cost", "value": 2.0},
    ]


# Today both return "high": ``_WORST_FIRST[0]`` is the strongest level. Strict,
# so the fix flips these to passing and the markers have to come off.
@pytest.mark.xfail(strict=True, reason="unknown confidence falls back to high, not low")
def test_refactoring_unknown_confidence_counts_as_low() -> None:
    steps = [SimpleNamespace(confidence="high"), SimpleNamespace(confidence="bogus")]
    assert refactoring_rank.weakest_confidence(steps) == "low"


@pytest.mark.xfail(strict=True, reason="empty step set falls back to high, not low")
def test_refactoring_empty_steps_count_as_low() -> None:
    assert refactoring_rank.weakest_confidence([]) == "low"


def test_perf_rank_keeps_its_shape() -> None:
    assert perf_rank.weakest_provenance({"direct", "name-fallback"}) == "name-fallback"
    assert perf_rank.why_ranked(
        {"boundary_kind": 4, "provenance": 0, "multiplier_shape": 4}, {"boundary_kind": "db"}
    ) == (
        {"factor": "boundary_kind", "value": "db", "points": 4},
        {"factor": "multiplier_shape", "value": None, "points": 4},
    )
