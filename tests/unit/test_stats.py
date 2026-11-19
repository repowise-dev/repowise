"""Tests for the one statistics module."""

from __future__ import annotations

import math
import random
import time
from typing import Any

import pytest

from repowise.core.stats import bootstrap_median_ratio, percentile, wilson_interval

# -- percentile parity ---------------------------------------------------------
# Verbatim copies of the private percentile helpers that callers used before
# they moved to ``percentile``. Each caller's output must not change.


def _old_actions_context(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))]


def _old_mcp_latency(sorted_values: list[int], pct: int) -> int:
    rank = max(1, math.ceil(pct / 100 * len(sorted_values)))
    return sorted_values[rank - 1]


def _old_health_p80(counts: list[int]) -> int | None:
    if not counts:
        return None
    counts = sorted(counts)
    idx_p80 = min(len(counts) - 1, max(0, int(0.8 * len(counts))))
    return counts[idx_p80]


def _old_perf_threshold(values: list[int], pct: float) -> int:
    if not values:
        return 1 << 30
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(pct * len(ordered))))
    return ordered[idx]


def _samples() -> list[list[int]]:
    rng = random.Random(7)
    out: list[list[int]] = [[], [1], [3, 3], [5, 1], list(range(10)), list(range(100))]
    for _ in range(300):
        size = rng.randint(1, 120)
        out.append([rng.randint(0, 50) for _ in range(size)])
    return out


@pytest.mark.parametrize("q", [0.9, 0.95])
def test_percentile_matches_actions_context(q: float) -> None:
    for values in _samples():
        assert percentile(values, q, default=0) == _old_actions_context(values, q)


@pytest.mark.parametrize("pct", [50, 90])
def test_percentile_matches_mcp_latency(pct: int) -> None:
    for values in _samples():
        if values:
            got = percentile(values, pct / 100, method="nearest_rank")
            assert got == _old_mcp_latency(sorted(values), pct)


def test_percentile_matches_health_p80() -> None:
    for values in _samples():
        assert percentile(values, 0.8) == _old_health_p80(values)


@pytest.mark.parametrize("q", [0.8, 0.99])
def test_percentile_matches_perf_threshold(q: float) -> None:
    for values in _samples():
        assert percentile(values, q, default=1 << 30) == _old_perf_threshold(values, q)


def test_percentile_edges() -> None:
    assert percentile([], 0.5) is None
    assert percentile([], 0.5, default=0) == 0
    assert percentile([4, 1, 9], 0.0) == 1
    assert percentile([4, 1, 9], 1.0) == 9
    assert percentile([4, 1, 9], 1.0, method="nearest_rank") == 9
    assert percentile([4, 1, 9], 0.0, method="nearest_rank") == 1
    with pytest.raises(ValueError):
        percentile([1], 1.5)
    unknown: Any = "linear"
    with pytest.raises(ValueError):
        percentile([1], 0.5, method=unknown)


# -- Wilson ----------------------------------------------------------------------
# Newcombe (1998), "Two-sided confidence intervals for the single proportion",
# Table I, method 3 (score interval without continuity correction).


@pytest.mark.parametrize(
    ("k", "n", "low", "high"),
    [
        (81, 263, 0.2553, 0.3662),
        (15, 148, 0.0624, 0.1605),
        (0, 20, 0.0, 0.1611),
        (1, 29, 0.0061, 0.1718),
    ],
)
def test_wilson_textbook_values(k: int, n: int, low: float, high: float) -> None:
    got = wilson_interval(k, n)
    assert got.estimate == pytest.approx(k / n)
    assert got.low == pytest.approx(low, abs=5e-5)
    assert got.high == pytest.approx(high, abs=5e-5)
    assert (got.n, got.method, got.confidence) == (n, "wilson", 0.95)


def test_wilson_edges() -> None:
    empty = wilson_interval(0, 0)
    assert (empty.estimate, empty.low, empty.high, empty.n) == (0.0, 0.0, 1.0, 0)
    none = wilson_interval(0, 50)
    assert none.low == 0.0 and 0.0 < none.high < 0.1
    full = wilson_interval(50, 50)
    assert full.high == 1.0 and 0.9 < full.low < 1.0
    with pytest.raises(ValueError):
        wilson_interval(3, 2)
    with pytest.raises(ValueError):
        wilson_interval(-1, 2)
    with pytest.raises(ValueError):
        wilson_interval(1, 2, confidence=1.0)


def test_wilson_wider_at_higher_confidence() -> None:
    narrow = wilson_interval(20, 100, confidence=0.9)
    wide = wilson_interval(20, 100, confidence=0.99)
    assert wide.low < narrow.low and wide.high > narrow.high


# -- bootstrap ratio ---------------------------------------------------------------


def test_bootstrap_is_deterministic_for_a_seed() -> None:
    rng = random.Random(1)
    head = [rng.uniform(1.0, 2.0) for _ in range(20)]
    base = [rng.uniform(1.0, 2.0) for _ in range(20)]
    first = bootstrap_median_ratio(head, base, seed=3)
    assert bootstrap_median_ratio(head, base, seed=3) == first
    assert bootstrap_median_ratio(head, base, seed=4) != first


def test_bootstrap_clear_regression_clears_one() -> None:
    rng = random.Random(2)
    base = [rng.gauss(100.0, 3.0) for _ in range(15)]
    head = [rng.gauss(150.0, 3.0) for _ in range(15)]
    got = bootstrap_median_ratio(head, base)
    assert got.low > 1.0
    assert got.low <= got.estimate <= got.high
    assert got.estimate == pytest.approx(1.5, rel=0.05)
    assert (got.n_head, got.n_base, got.confidence) == (15, 15, 0.95)


def test_bootstrap_identical_samples_cover_one() -> None:
    rng = random.Random(3)
    runs = [rng.gauss(100.0, 5.0) for _ in range(20)]
    got = bootstrap_median_ratio(runs, list(runs))
    assert got.estimate == 1.0
    assert got.low < 1.0 < got.high


def test_bootstrap_ignores_one_slow_outlier() -> None:
    base = [100.0, 101.0, 99.0, 100.5, 99.5, 100.0, 100.2]
    head = [*base[:-1], 10_000.0]
    got = bootstrap_median_ratio(head, base)
    assert got.estimate == pytest.approx(1.0, abs=0.01)
    assert got.low <= 1.0 <= got.high


def test_bootstrap_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        bootstrap_median_ratio([], [1.0])
    with pytest.raises(ValueError):
        bootstrap_median_ratio([1.0], [0.0])
    with pytest.raises(ValueError):
        bootstrap_median_ratio([1.0], [math.nan])
    with pytest.raises(ValueError):
        bootstrap_median_ratio([math.inf], [1.0])
    with pytest.raises(ValueError):
        bootstrap_median_ratio([1.0], [1.0], resamples=0)
    with pytest.raises(ValueError):
        bootstrap_median_ratio([1.0], [1.0], confidence=0.0)


def test_bootstrap_is_fast_at_fifty_per_side() -> None:
    rng = random.Random(4)
    head = [rng.uniform(1.0, 2.0) for _ in range(50)]
    base = [rng.uniform(1.0, 2.0) for _ in range(50)]
    start = time.perf_counter()
    bootstrap_median_ratio(head, base, resamples=2000)
    # Generous ceiling for slow CI machines; about 0.1 s on a laptop.
    assert time.perf_counter() - start < 2.0
