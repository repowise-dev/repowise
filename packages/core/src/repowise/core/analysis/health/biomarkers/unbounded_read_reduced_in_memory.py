"""Unbounded read reduced to one row per key in code.

Lifts ``perf.unbounded_reduction`` hits. ``PerfHit.path`` names the same-file
helper that does the reducing, when it is not the reading function itself.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "unbounded_read_reduced_in_memory"
_FIX = "select one row per key in the query (DISTINCT ON, a window function, or a view)"


class UnboundedReadReducedInMemoryDetector:
    name = _KIND
    category = "performance"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for hit in ctx.perf_hits:
            if hit.kind != _KIND:
                continue
            where = f"in {hit.path[0]}" if hit.path else "here"
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=Severity.LOW,
                    function_name=hit.function,
                    line_start=hit.line,
                    line_end=hit.line,
                    details={
                        "boundary_kind": hit.detail,
                        **({"reduced_in": hit.path[0]} if hit.path else {}),
                    },
                    reason=(
                        f"every row of an unbounded read is fetched, then reduced to one "
                        f"per key {where}; {_FIX}"
                    ),
                )
            )
        return out


BIOMARKER = UnboundedReadReducedInMemoryDetector()
