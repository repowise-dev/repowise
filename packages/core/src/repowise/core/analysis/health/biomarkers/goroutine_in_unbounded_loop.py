"""Goroutine-in-unbounded-loop — a ``go`` spawn per element of a range loop.

``for _, item := range items { go process(item) }`` fans out one goroutine per
item with no concurrency bound — the Go spawn-explosion anti-pattern (the fix is
a worker pool / bounded semaphore / ``errgroup`` with a limit). A ``performance``
dimension signal. Gated by the Go dialect to a ``range`` loop (a data-dependent
iteration count); a bare ``for {}`` accept loop or a ``for cond`` cursor is
excluded. This detector lifts the pre-collected hits into findings.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "goroutine_in_unbounded_loop"


class GoroutineInUnboundedLoopDetector:
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
                        "a goroutine is spawned per loop element with no concurrency "
                        "bound; use a worker pool or a bounded errgroup/semaphore"
                    ),
                )
            )
        return out


BIOMARKER = GoroutineInUnboundedLoopDetector()
