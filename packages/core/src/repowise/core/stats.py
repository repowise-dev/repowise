"""Percentiles and confidence intervals: the one owner for statistics in repowise.

Every percentile value, share interval and bootstrap interval a surface shows or
a rule compares against is computed here. Do not write a private
``_percentile`` next to a call site; add a ``method`` or a parameter here.

Stdlib only, so every package can import it without pulling in numpy.

``percentile`` returns a *value* (the q-th quantile of a sample). A percentile
*rank* (where one value sits among others) is a different question and is not
answered here.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, TypeVar

T = TypeVar("T", int, float)

PercentileMethod = Literal["inclusive_lower", "nearest_rank"]


def percentile(
    values: Iterable[T],
    q: float,
    *,
    method: PercentileMethod = "inclusive_lower",
    default: T | None = None,
) -> T | None:
    """The *q* quantile (``0 <= q <= 1``) of *values*, one of the inputs.

    ``inclusive_lower`` takes the element at index ``floor(q * n)`` of the
    sorted values, clamped to the last one: the convention the health
    thresholds use. ``nearest_rank`` takes the element at rank
    ``ceil(q * n)`` (at least 1), the textbook nearest-rank percentile used for
    latency summaries. Neither interpolates, so the result is always an
    observed value. *default* is returned for an empty input.
    """
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be in [0, 1], got {q}")
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return default
    if method == "inclusive_lower":
        index = int(q * n)
    elif method == "nearest_rank":
        index = max(1, math.ceil(q * n)) - 1
    else:
        raise ValueError(f"unknown percentile method {method!r}")
    return ordered[min(n - 1, index)]


@dataclass(frozen=True, slots=True)
class Interval:
    """A share of *n* samples with its confidence interval, on the 0..1 scale."""

    estimate: float
    low: float
    high: float
    n: int
    method: str
    confidence: float


@dataclass(frozen=True, slots=True)
class RatioInterval:
    """A ratio of two sample sets (``head / base``) with its confidence interval."""

    estimate: float
    low: float
    high: float
    n_head: int
    n_base: int
    method: str
    confidence: float


def _two_sided_z(confidence: float) -> float:
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    return statistics.NormalDist().inv_cdf(0.5 + confidence / 2)


def wilson_interval(k: int, n: int, *, confidence: float = 0.95) -> Interval:
    """Wilson score interval for *k* of *n* samples.

    Wilson rather than the normal approximation because the shares that matter
    sit near 0 (a function holding a few percent of samples), where the normal
    interval runs below zero and misplaces the mass. Wilson stays inside
    ``[0, 1]`` by construction. With ``n == 0`` nothing is known: the estimate
    is 0 and the interval is the whole ``[0, 1]``.
    """
    if n < 0 or k < 0 or k > n:
        raise ValueError(f"need 0 <= k <= n, got k={k} n={n}")
    if n == 0:
        return Interval(0.0, 0.0, 1.0, 0, "wilson", confidence)
    z = _two_sided_z(confidence)
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    # The bounds are exactly 0 at k == 0 and exactly 1 at k == n; floating
    # point would otherwise leave a stray 1e-17.
    low = 0.0 if k == 0 else max(0.0, center - half)
    high = 1.0 if k == n else min(1.0, center + half)
    return Interval(p, low, high, n, "wilson", confidence)


def bootstrap_median_ratio(
    head: Sequence[float],
    base: Sequence[float],
    *,
    confidence: float = 0.95,
    resamples: int = 5000,
    seed: int = 0,
) -> RatioInterval:
    """``median(head) / median(base)`` with a percentile bootstrap interval.

    Medians rather than means because run times are right-skewed: one slow run
    (a cold cache, a GC pause, a busy machine) moves a mean far more than the
    typical run moved, and a ratio of means would report that run as a
    regression. Each side is resampled on its own, as the two sets are
    independent runs. *seed* makes the interval reproducible; the same inputs
    and seed always give the same bounds. All values must be positive.
    """
    if not head or not base:
        raise ValueError("both sample sets need at least one value")
    if not all(math.isfinite(v) and v > 0 for v in (*head, *base)):
        raise ValueError("run times must be positive and finite")
    if resamples < 1:
        raise ValueError(f"resamples must be at least 1, got {resamples}")
    _two_sided_z(confidence)  # validates the range
    rng = random.Random(seed)
    median = statistics.median
    ratios = sorted(
        median(rng.choices(head, k=len(head))) / median(rng.choices(base, k=len(base)))
        for _ in range(resamples)
    )
    # The same number of resampled ratios is cut from each tail, so the
    # interval is symmetric in rank.
    cut = int((1.0 - confidence) / 2 * resamples)
    return RatioInterval(
        estimate=median(head) / median(base),
        low=ratios[cut],
        high=ratios[resamples - 1 - cut],
        n_head=len(head),
        n_base=len(base),
        method=f"bootstrap_median_ratio ({resamples} resamples)",
        confidence=confidence,
    )
