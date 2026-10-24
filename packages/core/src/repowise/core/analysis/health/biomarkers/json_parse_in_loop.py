"""JSON-deep-clone-in-loop: ``JSON.parse(JSON.stringify(x))`` every iteration.

The round-trip clone serializes and re-parses the whole value each pass (use
``structuredClone`` or clone once outside the loop). The TS/JS dialect fires only
on that clone: a bare ``JSON.parse`` or ``JSON.stringify`` per iteration usually
converts a distinct payload, which is necessary work. A ``performance`` dimension
signal, shipped advisory. This detector lifts the hits into findings.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "json_parse_in_loop"


class JsonParseInLoopDetector:
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
                    details=hit.loop_facts(),
                    reason=(
                        "a JSON.parse(JSON.stringify(...)) deep clone runs every loop "
                        "iteration; use structuredClone or clone once outside the loop"
                    ),
                )
            )
        return out


BIOMARKER = JsonParseInLoopDetector()
