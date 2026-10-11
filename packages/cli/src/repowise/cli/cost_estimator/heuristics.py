"""Backward-compatible re-export; the module lives in core now."""

from repowise.core.cost_estimator.heuristics import (
    HEURISTIC_VARIANCE,
    heuristic_tokens,
)

__all__ = ["HEURISTIC_VARIANCE", "heuristic_tokens"]
