"""Nested-loop I/O — an execution sink in the inner body of a nested loop.

A database / network / filesystem / subprocess round-trip that runs inside *two*
levels of data-dependent loop pays O(n·m) round-trips — the quadratic cousin of
``io_in_loop``. The nesting is the precision lever: a sink two loops deep is
almost never incidental, so this rides alongside ``io_in_loop`` un-gated (it does
NOT need the centrality gate the bare O(n^2) marker does).

A ``performance`` dimension signal. This detector lifts the pre-collected,
loop-depth-scoped hits (``complexity.walker._collect_perf_hits``, emitted only at
``loop_depth >= 2``) into findings — unsupported languages yield nothing.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "nested_loop_with_io"

_BOUNDARY_PHRASING: dict[str, str] = {
    "db": "a database call",
    "network": "a network call",
    "filesystem": "a filesystem call",
    "subprocess": "a subprocess spawn",
    "lock": "a lock acquisition",
}


class NestedLoopWithIoDetector:
    name = _KIND
    category = "performance"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for hit in ctx.perf_hits:
            if hit.kind != _KIND:
                continue
            phrasing = _BOUNDARY_PHRASING.get(hit.detail, "an I/O call")
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=Severity.MEDIUM,
                    function_name=hit.function,
                    line_start=hit.line,
                    line_end=hit.line,
                    details={"boundary_kind": hit.detail},
                    reason=(
                        f"{phrasing} runs inside a nested loop (O(n·m) round-trips); "
                        "batch the inner query or restructure the loops"
                    ),
                )
            )
        return out


BIOMARKER = NestedLoopWithIoDetector()
