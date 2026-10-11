"""Effort buckets: the size of a change in code lines (NLOC), as S / M / L / XL.

The one definition every surface sizes work with: the refactoring detectors,
the CLI's refactoring targets and the server's work queue. Dependency-free so
a surface can import it without loading the refactoring detectors.
"""

from __future__ import annotations

EFFORT_ORDER = ("S", "M", "L", "XL")
"""The buckets, smallest first."""

_CEILINGS = tuple(zip((40, 150, 400), EFFORT_ORDER[:-1], strict=True))

EFFORT_WEIGHT = dict(zip(EFFORT_ORDER, (1, 2, 3, 5), strict=True))
"""Each bucket's divisor in ``impact_per_effort``, and the order a ``max_effort`` cap reads."""


def effort_bucket(nloc: int) -> str:
    """Map a target's NLOC to its effort bucket."""
    for ceiling, label in _CEILINGS:
        if nloc <= ceiling:
            return label
    return "XL"


__all__ = ["EFFORT_ORDER", "EFFORT_WEIGHT", "effort_bucket"]
