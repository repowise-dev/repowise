"""IO-in-loop — an execution sink at an I/O boundary reached from a loop body.

The core of the ``performance`` dimension: a database round-trip, HTTP call,
filesystem read, or subprocess spawn that runs **once per loop iteration** (the
classic N+1 shape). One finding per occurrence, anchored to its line and tagged
with the boundary kind.

Two cases share this detector and the same ``performance`` budget:

  * **same-function** (Tier A, PR3) — the sink is written directly inside the
    loop; ``PerfHit.path`` is empty.
  * **cross-function** (Tier B, PR4) — the loop calls a helper that, within a
    few call hops, executes the sink; ``PerfHit.path`` carries the resolved
    ``A -> ... -> sink`` symbol chain for explainability. This is the
    interprocedural case file-local linters cannot see.

This is a *high-precision, low-recall* signal by design (measured 79% on the
Phase-0 gate, CI 68-87). The precision comes from three constraints enforced
upstream in the walker's perf pass (``complexity.perf_walk._collect_perf_hits``)
and the boundary classifier (``perf.io_boundaries``):

  1. **Loop-body scoping** — only calls under a loop's ``body`` field count; a
     query in the ``for x in q.all()`` *header* runs once, not per iteration.
  2. **Execution-sink gating** — ``select().where()`` query *builders* are not
     round-trips; only ``.execute`` / ``.scalars`` / ``.commit`` / awaited HTTP
     / ``subprocess.run`` / ``fetch`` and friends are.
  3. **Dependency classification** — the callee resolves to a *classified*
     external boundary via the shared ``io_kind`` table (import bridge), not a
     bare name heuristic.

This detector just lifts the pre-collected, pre-gated hits into findings —
unsupported languages and parse failures yield nothing, never a false positive.
"""

from __future__ import annotations

from ..complexity import PerfHit
from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "io_in_loop"

_BOUNDARY_PHRASING: dict[str, str] = {
    "db": "a database call",
    "network": "a network call",
    "filesystem": "a filesystem call",
    "subprocess": "a subprocess spawn",
    "lock": "a lock acquisition",
}


class IoInLoopDetector:
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
                        reason=f"{phrasing} runs once per loop iteration (N+1 / IO-in-loop)",
                    )
                )
        return out

    @staticmethod
    def _cross_function(hit: PerfHit, phrasing: str) -> BiomarkerResult:
        # Render the resolved symbol chain (``file.py::A`` -> ...) as bare
        # function names for the reason, and keep the full path in details.
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
                f"{phrasing} is reached once per loop iteration through "
                f"{chain} (cross-function N+1)"
            ),
        )


BIOMARKER = IoInLoopDetector()
