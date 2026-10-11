"""God Class — a class that is large, has many methods, and hides a brain.

A structural module smell: a single class that has accreted too many
responsibilities. We flag when **all three** hold:

- ``total_nloc ≥ 200``   — the class is large
- ``method_count ≥ 15``  — it has many methods
- it contains at least one *brain method* (a method with ``nloc ≥ 70`` and
  ``ccn ≥ 9``, reusing ``brain_method``'s structural floor) — the size
  isn't just a flat data holder; real logic concentrates inside it.

Requiring a brain method (not just size) keeps large-but-flat classes
(config tables, generated DTOs) from firing. Class-level metrics come from
the walker (``ClassComplexity``); the biomarker simply doesn't fire for
languages that don't expose class nodes.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

# Reused from brain_method's structural floor.
_BRAIN_NLOC = 70
_BRAIN_CCN = 9


class GodClassDetector:
    name = "god_class"
    category = "structural_complexity"

    _NLOC_THRESHOLD = 200
    _METHOD_THRESHOLD = 15

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for cls in ctx.class_metrics:
            if cls.total_nloc < self._NLOC_THRESHOLD:
                continue
            if cls.method_count < self._METHOD_THRESHOLD:
                continue
            # Find the actual brain method (the one that satisfies the gate)
            # and quote *its* CCN — not the class-wide ``max_method_ccn``, which
            # can belong to a short, high-CCN method that never passed the gate
            # (that would point a reviewer at the wrong function).
            brain_ccn, brain_name = max(
                (
                    (m.ccn, m.name)
                    for m in cls.methods
                    if m.nloc >= _BRAIN_NLOC and m.ccn >= _BRAIN_CCN
                ),
                default=(0, None),
            )
            if brain_name is None:
                continue

            if cls.total_nloc >= 400 and cls.method_count >= 25:
                severity = Severity.CRITICAL
            elif cls.total_nloc >= 300 or cls.method_count >= 20:
                severity = Severity.HIGH
            else:
                severity = Severity.MEDIUM

            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=severity,
                    function_name=cls.name,
                    line_start=cls.start_line,
                    line_end=cls.end_line,
                    details={
                        "class_name": cls.name,
                        "total_nloc": cls.total_nloc,
                        "method_count": cls.method_count,
                        "max_method_ccn": cls.max_method_ccn,
                        "brain_method_ccn": brain_ccn,
                        "brain_method_name": brain_name,
                    },
                    reason=(
                        f"{cls.name} is a god class: {cls.total_nloc} lines across "
                        f"{cls.method_count} methods, including a brain method "
                        f"({brain_name}, CCN {brain_ccn})"
                    ),
                )
            )
        return out


BIOMARKER = GodClassDetector()
