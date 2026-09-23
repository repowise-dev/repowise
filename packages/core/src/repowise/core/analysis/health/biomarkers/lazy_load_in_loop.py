"""Lazily-declared relationship read on each iteration of a loop.

Lifts ``perf.lazy_load`` hits. ``PerfHit.path`` carries ``(subject, fix)``: the
relationship read (``Incident.owner``) and the eager load that replaces it
(``selectinload(Incident.owner)``, ``select_related("owner")``).
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "lazy_load_in_loop"


class LazyLoadInLoopDetector:
    name = _KIND
    category = "performance"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for hit in ctx.perf_hits:
            if hit.kind != _KIND:
                continue
            subject, fix = hit.path
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=Severity.LOW,
                    function_name=hit.function,
                    line_start=hit.line,
                    line_end=hit.line,
                    details={
                        "boundary_kind": hit.detail,
                        "relationship": subject,
                        "eager_load": fix,
                        **hit.loop_facts(),
                    },
                    reason=(
                        f"{subject} is loaded lazily on each iteration of this loop, one "
                        f"query per row; load it with the rows: {fix}"
                    ),
                )
            )
        return out


BIOMARKER = LazyLoadInLoopDetector()
