"""JSON-parse/stringify-in-loop — serialize/deserialize every iteration.

``JSON.parse`` / ``JSON.stringify`` inside a loop re-serializes each pass; the
``JSON.parse(JSON.stringify(x))`` deep-clone in a loop is the canonical waste
(use ``structuredClone`` or hoist the parse). A ``performance`` dimension signal,
shipped advisory: per-iteration parsing of *distinct* payloads is sometimes
necessary work, so the centrality ranker downranks the cold sites. This detector
lifts the hits into findings.
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
                    details={},
                    reason=(
                        "JSON.parse/stringify runs every loop iteration; hoist the "
                        "serialization or use structuredClone for deep copies"
                    ),
                )
            )
        return out


BIOMARKER = JsonParseInLoopDetector()
