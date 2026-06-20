"""Blocking I/O under a lock — an I/O round-trip reached while a lock is held.

Holding a lock across a database / network / filesystem / subprocess call is a
classic throughput killer: every other thread that needs the lock blocks for the
full duration of the I/O wait, so the critical section's latency becomes the
whole system's serialization point. The fix is almost always to do the I/O
outside the lock and only take the lock to mutate shared state.

Two cases share this detector and the same ``performance`` budget:

  * **same-function** — the sink is lexically inside a block-scoped lock
    (C# ``lock (x) {}`` / Java ``synchronized (x) {}``); ``PerfHit.path`` empty.
  * **cross-function** — the lock-holding function calls a helper that, within a
    few hops, executes the sink; ``PerfHit.path`` carries the resolved
    ``A -> ... -> sink`` chain (``perf.gated.collect_blocking_io_under_lock``,
    reusing the sink-agnostic reachability engine with a lock→I/O entry set).

A ``performance`` dimension signal. This detector lifts the pre-collected hits
into findings — unsupported languages and parse failures yield nothing.
"""

from __future__ import annotations

from ..complexity import PerfHit
from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "blocking_io_under_lock"

_BOUNDARY_PHRASING: dict[str, str] = {
    "db": "a database call",
    "network": "a network call",
    "filesystem": "a filesystem call",
    "subprocess": "a subprocess spawn",
    "lock": "a lock acquisition",
}


class BlockingIoUnderLockDetector:
    name = _KIND
    category = "performance"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for hit in ctx.perf_hits:
            if hit.kind != _KIND:
                continue
            phrasing = _BOUNDARY_PHRASING.get(hit.detail, "an I/O call")
            if hit.path:
                out.append(self._cross_function(hit, phrasing))
            else:
                out.append(
                    BiomarkerResult(
                        biomarker_type=self.name,
                        severity=Severity.MEDIUM,
                        function_name=hit.function,
                        line_start=hit.line,
                        line_end=hit.line,
                        details={"boundary_kind": hit.detail, "cross_function": False},
                        reason=(
                            f"{phrasing} runs while a lock is held; move the I/O "
                            "outside the critical section to avoid serializing "
                            "every thread on the round-trip"
                        ),
                    )
                )
        return out

    @staticmethod
    def _cross_function(hit: PerfHit, phrasing: str) -> BiomarkerResult:
        names = [seg.rsplit("::", 1)[-1] for seg in hit.path]
        chain = " -> ".join(names)
        return BiomarkerResult(
            biomarker_type=_KIND,
            severity=Severity.MEDIUM,
            function_name=hit.function,
            line_start=hit.line,
            line_end=hit.line,
            details={
                "boundary_kind": hit.detail,
                "cross_function": True,
                "path": list(hit.path),
            },
            reason=(
                f"{phrasing} is reached while a lock is held, through "
                f"{chain}; move the I/O outside the critical section"
            ),
        )


BIOMARKER = BlockingIoUnderLockDetector()
