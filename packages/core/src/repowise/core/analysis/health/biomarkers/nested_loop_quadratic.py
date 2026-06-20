"""Nested-loop quadratic — an O(n^2) loop shape in a hot, central function.

A data-dependent loop nested inside another is an O(n^2) algorithmic shape. On
its own that is a LOW-precision signal: triangular bounds, tiny-n loops, and
two-pass algorithms over small collections are all fine. The backlog deferred
the bare version for exactly that reason. The unlock is the **centrality gate**:
the walker emits this as a candidate, and ``perf.gated.apply_centrality_gate``
drops every hit whose enclosing function is not hot (top-quintile call-graph
centrality or a churny/hotspot file). What reaches this detector is already
gated, so a surviving hit is a quadratic shape sitting on a frequently-exercised
path — worth a look.

A ``performance`` dimension signal. This detector only lifts the (already
centrality-gated) hits into findings.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "nested_loop_quadratic"


class NestedLoopQuadraticDetector:
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
                        "a loop nested inside another loop (O(n^2)) sits in a "
                        "hot/central function; check the inner bound or use a "
                        "set/map lookup if it is a search"
                    ),
                )
            )
        return out


BIOMARKER = NestedLoopQuadraticDetector()
