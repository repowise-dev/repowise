"""Nested Complexity — deeply indented control flow.

Flags functions whose maximum nesting depth exceeds 4. Deep nesting is
strongly correlated with defect density in published studies.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext


class NestedComplexityDetector:
    name = "nested_complexity"
    category = "structural_complexity"

    _NESTING_THRESHOLD = 4

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for fn in ctx.function_metrics.values():
            if fn.max_nesting < self._NESTING_THRESHOLD:
                continue
            severity = (
                Severity.CRITICAL
                if fn.max_nesting >= 7
                else Severity.HIGH
                if fn.max_nesting >= 5
                else Severity.MEDIUM
            )
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=severity,
                    function_name=fn.name,
                    line_start=fn.start_line,
                    line_end=fn.end_line,
                    details={
                        "max_nesting": fn.max_nesting,
                        "ccn": fn.ccn,
                        "cognitive": fn.cognitive,
                    },
                    reason=f"{fn.name} nests {fn.max_nesting} levels deep",
                )
            )
        return out


BIOMARKER = NestedComplexityDetector()
