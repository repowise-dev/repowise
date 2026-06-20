"""Hot-path sync I/O — a blocking I/O call in a hot, request-reachable function.

A synchronous (non-awaited) database / network / filesystem / subprocess
round-trip blocks its thread for the duration of the call. Outside a loop that is
usually fine — but in a *hot, central* function (one on many request paths) the
blocking wait is paid on every request, a latency risk a loop-only detector never
sees. This generalizes the performance pillar beyond loops using the centrality
the engine already computes.

The walker emits every loop-depth-0 blocking sink as a candidate; the centrality
gate (``perf.gated.apply_centrality_gate``) keeps only those in a top-quintile-
central or churny function. A ``performance`` dimension signal — this detector
lifts the (already-gated) hits into findings.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "hot_path_sync_io"

_BOUNDARY_PHRASING: dict[str, str] = {
    "db": "a blocking database call",
    "network": "a blocking network call",
    "filesystem": "a blocking filesystem call",
    "subprocess": "a blocking subprocess spawn",
    "lock": "a blocking lock acquisition",
}


class HotPathSyncIoDetector:
    name = _KIND
    category = "performance"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for hit in ctx.perf_hits:
            if hit.kind != _KIND:
                continue
            phrasing = _BOUNDARY_PHRASING.get(hit.detail, "a blocking I/O call")
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=Severity.LOW,
                    function_name=hit.function,
                    line_start=hit.line,
                    line_end=hit.line,
                    details={"boundary_kind": hit.detail},
                    reason=(
                        f"{phrasing} runs on a hot, request-reachable path; "
                        "its latency is paid on every call through this function"
                    ),
                )
            )
        return out


BIOMARKER = HotPathSyncIoDetector()
