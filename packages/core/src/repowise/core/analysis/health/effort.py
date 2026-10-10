"""Effort buckets: the size of a change in code lines (NLOC), as S / M / L / XL.

The one definition every surface sizes work with: the refactoring detectors,
the CLI's refactoring targets and the server's work queue. Dependency-free so
a surface can import it without loading the refactoring detectors.
"""

from __future__ import annotations

_CEILINGS: tuple[tuple[int, str], ...] = ((40, "S"), (150, "M"), (400, "L"))

EFFORT_WEIGHT = {"S": 1, "M": 2, "L": 3, "XL": 5}
"""Each bucket's divisor in ``impact_per_effort``, and the order a ``max_effort`` cap reads."""


def effort_bucket(nloc: int) -> str:
    """Map a target's NLOC to its effort bucket."""
    for ceiling, label in _CEILINGS:
        if nloc <= ceiling:
            return label
    return "XL"


__all__ = ["EFFORT_WEIGHT", "effort_bucket"]
