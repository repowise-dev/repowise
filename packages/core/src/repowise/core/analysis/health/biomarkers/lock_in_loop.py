"""Lock-in-loop — a lock acquired on every loop iteration.

Acquiring a mutex / lock each iteration (``lock.acquire`` / ``mu.Lock`` /
``synchronized`` / ``lock(x){}``) serializes the loop body and is a contention
hotspot: the critical section runs N times, and under multi-threading the
repeated hand-off dominates. A ``performance`` dimension signal that activates
the ``lock`` I/O boundary kind defined in ``io_kind.py``.

Precision-first: only the acquisition verb fires (never ``release`` / ``unlock``,
which would double-count the same critical section), and the per-language dialect
gates it on a receiver method / lock statement. This detector lifts the
pre-collected hits into findings.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "lock_in_loop"


class LockInLoopDetector:
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
                    severity=Severity.LOW,
                    function_name=hit.function,
                    line_start=hit.line,
                    line_end=hit.line,
                    details={},
                    reason=(
                        "a lock is acquired every loop iteration; hoist the "
                        "lock outside the loop or batch the critical section"
                    ),
                )
            )
        return out


BIOMARKER = LockInLoopDetector()
