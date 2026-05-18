"""Large Method — function bodies that exceed a healthy line budget.

Fires on raw NLOC, independent of complexity. A 200-line function with
CCN 2 still hurts readability and tests; a 60-line function with CCN 12
is the `complex_method` case. Both can fire on the same function — the
scorer caps the size_and_complexity category so they don't double-count.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext


class LargeMethodDetector:
    name = "large_method"
    category = "size_and_complexity"

    _NLOC_THRESHOLD = 60

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for fn in ctx.function_metrics.values():
            if fn.nloc < self._NLOC_THRESHOLD:
                continue
            severity = (
                Severity.CRITICAL
                if fn.nloc >= 200
                else Severity.HIGH
                if fn.nloc >= 120
                else Severity.MEDIUM
                if fn.nloc >= 90
                else Severity.LOW
            )
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=severity,
                    function_name=fn.name,
                    line_start=fn.start_line,
                    line_end=fn.end_line,
                    details={
                        "nloc": fn.nloc,
                        "ccn": fn.ccn,
                    },
                    reason=f"{fn.name} is {fn.nloc} lines long",
                )
            )
        return out


BIOMARKER = LargeMethodDetector()
