"""Long function — function bodies that exceed a healthy line budget.

Counts lines of code, JSX markup lines excluded, once the judged CCN shows some
branching: a long flat body (a data literal, a dispatch of one-line arms) is
layout. ``complex_method`` covers short but tangled functions.
"""

from __future__ import annotations

from ..complexity.dispatch import judged_ccn
from ..complexity.models import FunctionComplexity
from ..models import Severity
from .base import BiomarkerResult, FileContext, shape_details


def _reason(fn: FunctionComplexity, code_lines: int, span: int) -> str:
    besides = f" besides {fn.markup_nloc} of JSX markup" if fn.markup_nloc else ""
    return f"{fn.name} is {code_lines} lines of code{besides} ({span} with blanks and comments)"


class LargeMethodDetector:
    name = "large_method"
    category = "size_and_complexity"

    _NLOC_THRESHOLD = 60
    # 3, not 2: a flat ``match`` of single-expression arms scores CCN 2 (one point
    # for the keyword), the layout artefact this floor filters. A long body with
    # one ``if`` lands at 2 as well and is skipped, which is weak evidence anyway.
    _CCN_FLOOR = 3

    @classmethod
    def severity_for(cls, ccn: int, nloc: int) -> Severity | None:
        """The severity a function of this shape earns, ``None`` below the bar."""
        if nloc < cls._NLOC_THRESHOLD or ccn < cls._CCN_FLOOR:
            return None
        return (
            Severity.CRITICAL
            if nloc >= 200
            else Severity.HIGH
            if nloc >= 120
            else Severity.MEDIUM
            if nloc >= 90
            else Severity.LOW
        )

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for fn in ctx.all_functions:
            code_lines = fn.nloc - fn.markup_nloc
            judged = judged_ccn(fn)
            severity = self.severity_for(judged, code_lines)
            if severity is None:
                continue
            span = fn.end_line - fn.start_line + 1
            details = {"nloc": fn.nloc, "ccn": fn.ccn, "span": span, **shape_details(fn)}
            if judged != fn.ccn:
                details["judged_ccn"] = judged
            if fn.markup_nloc:
                details["markup_nloc"] = fn.markup_nloc
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=severity,
                    function_name=fn.name,
                    line_start=fn.start_line,
                    line_end=fn.end_line,
                    details=details,
                    reason=_reason(fn, code_lines, span),
                )
            )
        return out


BIOMARKER = LargeMethodDetector()
