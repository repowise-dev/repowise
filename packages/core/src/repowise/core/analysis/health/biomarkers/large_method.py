"""Large Method — function bodies that exceed a healthy line budget.

Fires on lines of code once a function carries *some* branching, so it measures
length-with-substance rather than pure size. A long but flat body (a big
config/data literal, a wall of sequential assignments) is a layout artefact,
excluded by the ``_CCN_FLOOR`` gate on the judged CCN, which also reads a long
dispatch of one-line arms as flat (``complexity/dispatch.py``). Lines holding
only JSX markup are not code here. The short-but-tangled case is
``complex_method``; both can fire on one function, and the scorer caps their
shared category.
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
            severity = self.severity_for(judged_ccn(fn), code_lines)
            if severity is None:
                continue
            span = fn.end_line - fn.start_line + 1
            details = {"nloc": fn.nloc, "ccn": fn.ccn, "span": span, **shape_details(fn)}
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
