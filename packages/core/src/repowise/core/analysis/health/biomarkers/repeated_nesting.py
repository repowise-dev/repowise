"""Repeated nesting: several separate nested blocks side by side in one function.

Not one deep block (``nested_complexity``) and not one long list of branches
(``complex_method``): multiple medium-depth blocks in sequence. Reads the
walker's ``bumps`` count, the top-level body statements that reach nesting
depth 2 or more, and fires when it crosses a threshold while CCN is non-trivial.
"""

from __future__ import annotations

from ..complexity.dispatch import judged_ccn
from ..models import Severity
from .base import BiomarkerResult, FileContext


class RepeatedNestingDetector:
    name = "repeated_nesting"
    category = "structural_complexity"

    _BUMP_THRESHOLD = 3
    _CCN_THRESHOLD = 5

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for fn in ctx.all_functions:
            if fn.bumps < self._BUMP_THRESHOLD:
                continue
            if judged_ccn(fn) < self._CCN_THRESHOLD:
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
                        f"{fn.name} has {fn.bumps} nested blocks at the same level"
                    ),
                )
            )
        return out


BIOMARKER = RepeatedNestingDetector()
