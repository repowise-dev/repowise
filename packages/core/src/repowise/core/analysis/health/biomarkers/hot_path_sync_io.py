"""Hot-path sync I/O: a blocking I/O call in a hot, central function.

A synchronous (non-awaited) filesystem or subprocess call blocks its thread for
the duration of the call. Outside a loop that is usually fine, but in a *hot,
central* function (one many call sites go through) the wait is paid on every
one of those calls, a latency risk a loop-only detector never sees.

"Hot" is top-quintile direct-caller count in the execution graph
(``perf.ranking.PerfRanker``). That says the function is widely called, not
that a request reaches it: no request-handler entry set exists to prove that,
so the reason text claims only what the gate establishes. Code that serves no
request (tests, tooling, examples, generated or vendored code) never emits
(``perf.gated``). A ``performance`` dimension signal; this
detector lifts the (already-gated) hits into findings.
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
                        f"{phrasing} in one of the most-called functions in "
                        "this repo; every call through it waits for the I/O"
                    ),
                )
            )
        return out


BIOMARKER = HotPathSyncIoDetector()
