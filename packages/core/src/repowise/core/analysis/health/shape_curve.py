"""How the defect score aggregates code-shape findings.

Three rules, fitted together on the defect corpus so the headline stays
AUC-neutral while every improvement to a large function shows:

- A critical complexity, size or nesting finding deducts in proportion to how
  far past its critical threshold the function sits, so halving a CCN-50
  function pays even while it stays critical.
- Findings on one function share a group: the largest counts whole and every
  other one at :data:`FUNCTION_STACK_SHARE`, so one tangled function is not
  charged once per marker that sees it.
- A category's total follows a saturating curve, ``A * (1 - exp(-raw / A))``
  with ``A = CATEGORY_SATURATION x the category cap``, instead of stopping at
  the cap, so a second critical function still costs something.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

# The curve's asymptote as a multiple of the category cap, calibrated so the
# corpus mean score matches the hard-capped one.
CATEGORY_SATURATION: float = 3.09
FUNCTION_STACK_SHARE: float = 0.25

# Markers that describe one function's shape and so share its group.
FUNCTION_SHAPE_MARKERS: frozenset[str] = frozenset(
    {
        "complex_method",
        "large_method",
        "nested_complexity",
        "brain_method",
        "repeated_nesting",
        "complex_conditional",
        "primitive_obsession",
        "god_class",
        "low_cohesion",
    }
)

# The measure each continuous marker reads and the value its critical band starts at.
_CRITICAL_MEASURE: dict[str, tuple[str, float]] = {
    "complex_method": ("ccn", 25),
    "large_method": ("nloc", 200),
    "nested_complexity": ("max_nesting", 7),
}
_CRITICAL_BASE: float = 2.0


def continuous_base(biomarker: str, base: float, critical: bool, details: Mapping | None) -> float:
    """*base*, or for a critical sized finding past its threshold ``2.0 x value / threshold``."""
    measure = _CRITICAL_MEASURE.get(biomarker)
    if not critical or measure is None:
        return base
    key, threshold = measure
    value = (details or {}).get(key)
    if isinstance(value, (int, float)) and value > threshold:
        return _CRITICAL_BASE * value / threshold
    return base


def group_by_function(names: Sequence[tuple[str, str | None]], weighted: list[float]) -> None:
    """Scale *weighted* in place so each function's shape findings total max + share x rest.

    *names* is ``(biomarker, function_name)`` per entry, parallel to *weighted*.
    """
    groups: dict[str, list[int]] = {}
    for i, (biomarker, function) in enumerate(names):
        if function and biomarker in FUNCTION_SHAPE_MARKERS:
            groups.setdefault(function, []).append(i)
    for members in groups.values():
        total = sum(weighted[i] for i in members)
        if total <= 0:
            continue
        top = max(weighted[i] for i in members)
        scale = (top + FUNCTION_STACK_SHARE * (total - top)) / total
        for i in members:
            weighted[i] *= scale


def saturated(raw: float, cap: float) -> float:
    """A category's deduction under the saturating curve."""
    top = ceiling(cap)
    return top * (1.0 - math.exp(-raw / top)) if top > 0 else 0.0


def ceiling(cap: float) -> float:
    """The most a saturating category can ever deduct."""
    return CATEGORY_SATURATION * cap


__all__ = [
    "CATEGORY_SATURATION",
    "FUNCTION_SHAPE_MARKERS",
    "FUNCTION_STACK_SHARE",
    "ceiling",
    "continuous_base",
    "group_by_function",
    "saturated",
]
