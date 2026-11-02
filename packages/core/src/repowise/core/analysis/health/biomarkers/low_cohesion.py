"""Low Cohesion (LCOM4) — a class whose methods form unrelated clusters.

A cohesive class is one whose methods all collaborate around a shared set
of fields. LCOM4 (Hitz & Montazeri) measures the opposite: it builds a
graph whose nodes are the class's methods and whose edges link methods
that share an instance field or call one another, then counts the
connected components. ``lcom4 = 1`` is fully cohesive; ``lcom4 ≥ 2`` means
the class splits into method groups that don't talk to each other — a
candidate for splitting into smaller, single-responsibility classes.

The class-level metrics (including LCOM4) come from the walker
(``ClassComplexity``); see ``complexity/walker.py`` and ``complexity/README.md``
for the heuristic and its per-language limits. When a language doesn't
expose class/member-access nodes the walker reports ``lcom4 = 1`` (no
signal), so this biomarker simply doesn't fire there — never falsely.
"""

from __future__ import annotations

from ..complexity.class_analysis import cohesion_applies
from ..models import Severity
from .base import BiomarkerResult, FileContext


class LowCohesionDetector:
    name = "low_cohesion"
    category = "structural_complexity"

    # Ignore tiny classes and data holders — LCOM is only meaningful once
    # there are enough methods to splinter.
    _MIN_METHODS = 5

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        if not cohesion_applies(ctx.file_path, ctx.language):
            return out
        for cls in ctx.class_metrics:
            if cls.lcom4 < 2 or cls.method_count < self._MIN_METHODS:
                continue
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=_severity(cls.lcom4, cls.method_count),
                    function_name=cls.name,
                    line_start=cls.start_line,
                    line_end=cls.end_line,
                    details={
                        "class_name": cls.name,
                        "lcom4": cls.lcom4,
                        "method_count": cls.method_count,
                        "field_count": cls.field_count,
                    },
                    reason=(
                        f"{cls.name} has low cohesion (LCOM4={cls.lcom4}): its "
                        f"{cls.method_count} methods split into {cls.lcom4} "
                        f"groups that share no fields or calls"
                    ),
                )
            )
        return out


def _severity(lcom4: int, method_count: int) -> Severity:
    if lcom4 >= 4 and method_count >= 15:
        return Severity.CRITICAL
    if lcom4 >= 3 or method_count >= 20:
        return Severity.HIGH
    return Severity.MEDIUM


BIOMARKER = LowCohesionDetector()
