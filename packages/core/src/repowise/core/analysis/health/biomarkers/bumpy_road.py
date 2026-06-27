"""Bumpy Road — many independent regions of nested control flow.

A "bumpy road" function is one with several distinct nesting humps —
not one deep block (that's `nested_complexity`) and not one big linear
list of branches (that's `complex_method`) but multiple medium-depth
blocks sitting side by side.

We use the walker's ``bumps`` metric — the count of top-level body
statements that reach nesting depth ≥ 2 — and fire when it crosses a
configurable threshold while CCN is also non-trivial.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext


class BumpyRoadDetector:
    name = "bumpy_road"
    category = "structural_complexity"

    _BUMP_THRESHOLD = 3
    _CCN_THRESHOLD = 5

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for fn in ctx.function_metrics.values():
            if fn.bumps < self._BUMP_THRESHOLD:
                continue
            if fn.ccn < self._CCN_THRESHOLD:
                continue
            severity = (
                Severity.HIGH
                if fn.bumps >= 5
                else Severity.MEDIUM
                if fn.bumps >= 4
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
                        "bumps": fn.bumps,
                        "ccn": fn.ccn,
                        "max_nesting": fn.max_nesting,
                    },
                    reason=(
                        f"{fn.name} has {fn.bumps} nested blocks at the same level (bumpy road)"
                    ),
                )
            )
        return out


BIOMARKER = BumpyRoadDetector()
