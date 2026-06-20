"""Pandas-iterrows-in-loop — ``for _, row in df.iterrows():``.

``DataFrame.iterrows()`` boxes every row into a fresh ``Series``, making
row-by-row iteration an order of magnitude slower than a vectorized operation
(and slower than ``itertuples`` / ``to_dict("records")`` when per-row access is
unavoidable). The offending call sits in the loop *header*, so the body
loop-call markers miss it; the walker fires this via the dialect
``loop_iterable_call_marker`` hook against the loop node. A ``performance``
dimension signal, gated by the Python dialect to the distinctive ``iterrows``
method on a member-access receiver. This detector lifts the hits into findings.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "pandas_iterrows_in_loop"


class PandasIterrowsInLoopDetector:
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
                        "DataFrame.iterrows() boxes each row into a Series (row-by-row, slow); "
                        "prefer a vectorized operation, or itertuples()/to_dict('records') when "
                        "per-row access is unavoidable"
                    ),
                )
            )
        return out


BIOMARKER = PandasIterrowsInLoopDetector()
