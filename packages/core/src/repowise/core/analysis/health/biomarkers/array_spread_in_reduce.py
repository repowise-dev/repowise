"""Array-spread-in-reduce — ``arr.reduce((a, x) => [...a, x], [])``.

Spreading the accumulator into a fresh array / object on every reduce step
rebuilds the whole accumulator each iteration, turning an O(n) fold into O(n^2).
The fix is to mutate-and-return the accumulator (``a.push(x); return a``) or use
``Object.assign``. A ``performance`` dimension signal. Detected by the TS/JS
dialect only when the reduce callback spreads its OWN accumulator parameter, so
it fires regardless of an enclosing loop (the ``.reduce`` is itself the loop).
This detector lifts the hits into findings.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "array_spread_in_reduce"


class ArraySpreadInReduceDetector:
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
                    severity=Severity.MEDIUM,
                    function_name=hit.function,
                    line_start=hit.line,
                    line_end=hit.line,
                    details={},
                    reason=(
                        "spreading the accumulator into a new array/object each reduce "
                        "step rebuilds it every pass (O(n^2)); push-and-return instead"
                    ),
                )
            )
        return out


BIOMARKER = ArraySpreadInReduceDetector()
