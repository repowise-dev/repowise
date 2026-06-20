"""Pandas-concat-in-loop — ``pd.concat([acc, chunk])`` every iteration.

Concatenating a DataFrame inside a loop copies the entire accumulated frame each
pass, turning an O(n) build into O(n^2) (the documented pandas anti-pattern). The
fix is to collect the chunks in a list and ``pd.concat`` them once after the
loop. A ``performance`` dimension signal. Gated by the Python dialect to the
distinctive ``pd``/``pandas`` ``concat`` root. This detector lifts the hits into
findings.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "pd_concat_in_loop"


class PdConcatInLoopDetector:
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
                        "pd.concat in a loop copies the whole frame each pass (O(n^2)); "
                        "collect the chunks in a list and concat once after the loop"
                    ),
                )
            )
        return out


BIOMARKER = PdConcatInLoopDetector()
