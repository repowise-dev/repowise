"""Serial-await-in-loop — an awaited I/O sink run serially in a loop body.

``await``-ing an I/O round-trip inside a loop runs the calls one after another
when they could often be fanned out (``asyncio.gather`` / ``Promise.all`` /
``Task.WhenAll``). This is the *missed-concurrency* sibling of ``io_in_loop``: a
different remediation (parallelize) from batching, so it rides as a separate,
advisory signal alongside the N+1 finding rather than replacing it.

Advisory by design: static analysis cannot prove the iterations are independent
(a loop may genuinely need the awaited result before the next iteration), so the
finding suggests rather than asserts. ``detail`` carries the boundary kind. This
detector lifts the pre-collected hits into findings.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "serial_await_in_loop"

_BOUNDARY_PHRASING: dict[str, str] = {
    "db": "a database call",
    "network": "a network call",
    "filesystem": "a filesystem call",
    "subprocess": "a subprocess spawn",
    "lock": "a lock acquisition",
}


class SerialAwaitInLoopDetector:
    name = _KIND
    category = "performance"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for hit in ctx.perf_hits:
            if hit.kind != _KIND:
                continue
            phrasing = _BOUNDARY_PHRASING.get(hit.detail, "an awaited I/O call")
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=Severity.LOW,
                    function_name=hit.function,
                    line_start=hit.line,
                    line_end=hit.line,
                    details={"boundary_kind": hit.detail},
                    reason=(
                        f"{phrasing} is awaited serially in a loop; if the "
                        "iterations are independent, fan out with "
                        "gather / Promise.all"
                    ),
                )
            )
        return out


BIOMARKER = SerialAwaitInLoopDetector()
