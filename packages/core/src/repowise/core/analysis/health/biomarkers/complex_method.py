"""Complex Method — high cyclomatic complexity.

Flags functions with CCN ≥ 9. A conservative threshold for the boundary
between "ok" and "complex". A function that is mostly one dispatch on one
value is judged on the CCN outside it (``complexity/dispatch.py``).
"""

from __future__ import annotations

from ..complexity.dispatch import judged_ccn
from ..models import Severity
from .base import BiomarkerResult, FileContext, shape_details


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
            judged = judged_ccn(fn)
            severity = self.severity_for(judged, fn.nloc)
            if severity is None:
                continue
            details = {"ccn": fn.ccn, "cognitive": fn.cognitive, "nloc": fn.nloc}
            reason = f"{fn.name} has cyclomatic complexity {judged}"
            if judged != fn.ccn:
                details["judged_ccn"] = judged
                reason += f" ({fn.ccn} counting every arm of its dispatch)"
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=severity,
                    function_name=fn.name,
                    line_start=fn.start_line,
                    line_end=fn.end_line,
                    details={**details, **shape_details(fn)},
                    reason=reason,
                )
            )
        return out


BIOMARKER = ComplexMethodDetector()
