"""Complex Method — high cyclomatic complexity.

Flags functions with CCN ≥ 9. A conservative threshold for the boundary
between "ok" and "complex".
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext


class ComplexMethodDetector:
    name = "complex_method"
    category = "size_and_complexity"

    _CCN_THRESHOLD = 9

    @classmethod
    def severity_for(cls, ccn: int, nloc: int) -> Severity | None:
        """The severity a function of this shape earns, ``None`` below the bar."""
        if ccn < cls._CCN_THRESHOLD:
            return None
        return Severity.CRITICAL if ccn >= 25 else Severity.HIGH if ccn >= 15 else Severity.MEDIUM

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for fn in ctx.all_functions:
            severity = self.severity_for(fn.ccn, fn.nloc)
            if severity is None:
                continue
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=severity,
                    function_name=fn.name,
                    line_start=fn.start_line,
                    line_end=fn.end_line,
                    details={
                        "ccn": fn.ccn,
                        "cognitive": fn.cognitive,
                        "nloc": fn.nloc,
                    },
                    reason=f"{fn.name} has cyclomatic complexity {fn.ccn}",
                )
            )
        return out


BIOMARKER = ComplexMethodDetector()
