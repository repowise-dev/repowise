"""Ranking helpers both opportunity orders share.

``refactoring/opportunity_rank.py`` and ``perf/opportunity_rank.py`` rank
different units with different factor models; those models stay in their own
modules as data. What they share is the shape: a group is only as trustworthy
as its weakest member, and "why ranked" is the few largest non-zero factors.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def weakest(labels: Iterable[str], strength: Mapping[str, float]) -> str:
    """The least trusted of *labels*, ties broken by name.

    A label *strength* does not list counts as 0. Raises on an empty input,
    as ``min`` does: what an empty group means is the caller's policy.
    """
    return min(labels, key=lambda label: (strength.get(label, 0), label))


def top_factors(factors: Mapping[str, float], limit: int) -> list[tuple[str, float]]:
    """The *limit* largest non-zero factors by magnitude, ties broken by name."""
    ranked = sorted(
        ((name, value) for name, value in factors.items() if value),
        key=lambda item: (-abs(item[1]), item[0]),
    )
    return ranked[:limit]


__all__ = ["top_factors", "weakest"]
