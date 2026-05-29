"""Large Method — function bodies that exceed a healthy line budget.

Fires on NLOC once a function carries *some* branching, so it measures
length-with-substance rather than pure size. A long but perfectly flat
body (a big config/data literal, a wall of sequential assignments — CCN 1)
is a layout artefact, not a maintainability smell, and is excluded by the
``_CCN_FLOOR`` gate. The complementary case — a short-but-tangled 60-line
function with CCN 12 — is handled by ``complex_method``. Both can fire on
the same function; the scorer caps the size_and_complexity category so
they don't double-count.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext


class LargeMethodDetector:
    name = "large_method"
    category = "size_and_complexity"

    _NLOC_THRESHOLD = 60
    # Minimal branching required so a long-but-flat body (CCN 1, e.g. a big
    # config dict) doesn't read as a complexity smell. Anything with even a
    # single branch clears this floor. Keeps the trigger about substance,
    # not raw line count — directly targeting the size-confound critique.
    _CCN_FLOOR = 2

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for fn in ctx.function_metrics.values():
            if fn.nloc < self._NLOC_THRESHOLD or fn.ccn < self._CCN_FLOOR:
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
