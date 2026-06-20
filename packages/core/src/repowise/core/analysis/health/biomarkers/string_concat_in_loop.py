"""String-concat-in-loop — ``+=`` string accumulation inside a loop.

Building a string by repeated ``+=`` inside a loop is quadratic in many runtimes
(each concat copies the whole accumulated string); a buffer + ``join`` is linear.
A ``performance`` dimension signal, loop-form only — single-line concatenation is
compiler-optimized and not flagged.

Precision-first: the walker's perf pass flags this ONLY when the RHS is provably
a string literal / template (``s += "..."`` / ``s += f"{x}"`` / ``s += "a" + b``),
never an opaque ``s += chunk`` that could be numeric. This detector lifts the
pre-collected hits into findings.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "string_concat_in_loop"


class StringConcatInLoopDetector:
    name = _KIND
    category = "performance"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for hit in ctx.perf_hits:
            if hit.kind != _KIND:
                continue
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=Severity.LOW,
                    function_name=hit.function,
                    line_start=hit.line,
                    line_end=hit.line,
                    details={},
                    reason="string built by += in a loop; use a buffer / join for linear cost",
                )
            )
        return out


BIOMARKER = StringConcatInLoopDetector()
