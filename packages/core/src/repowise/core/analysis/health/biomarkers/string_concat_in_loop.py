"""String-concat-in-loop — ``+=`` string accumulation inside a loop.

Building a string by repeated ``+=`` inside a loop is quadratic in many runtimes
(each concat copies the whole accumulated string); a buffer + ``join`` is linear.
A ``performance`` dimension signal, loop-form only — single-line concatenation is
compiler-optimized and not flagged.

Precision-first: the walker's perf pass flags this ONLY when the RHS is provably
a string literal / template (``s += "..."`` / ``s += f"{x}"`` / ``s += "a" + b``),
never an opaque ``s += chunk`` that could be numeric. This detector lifts the
pre-collected hits into findings, except in test files: a test or benchmark that
builds its expected text or input data with ``+=`` is not code anyone speeds up.

One loop's appends to one named string are one edit, so they are one finding:
it sits on the first append and lists every appended line in ``details["lines"]``.
"""

from __future__ import annotations

from ..complexity import PerfHit
from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "string_concat_in_loop"


class StringConcatInLoopDetector:
    name = _KIND
    category = "performance"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        if ctx.is_test_material:
            return []
        groups: dict[tuple[str | None, int, int, str | int], list[PerfHit]] = {}
        for index, hit in enumerate(ctx.perf_hits):
            if hit.kind == _KIND:
                # A target with no quotable name (``buf[i] +=``) is not shown to
                # be the same string as another one, so it keeps its own row.
                target = hit.subject or index
                key = (hit.function, hit.func_start, hit.loop_line or hit.line, target)
                groups.setdefault(key, []).append(hit)
        return [self._finding(sorted(hits, key=lambda h: h.line)) for hits in groups.values()]

    def _finding(self, hits: list[PerfHit]) -> BiomarkerResult:
        first = hits[0]
        details = first.loop_facts()
        if len(hits) > 1:
            details["lines"] = [hit.line for hit in hits]
        return BiomarkerResult(
            biomarker_type=self.name,
            severity=Severity.LOW,
            function_name=first.function,
            line_start=first.line,
            # The first line only: a row with no enclosing symbol is identified
            # by its absolute lines, and the merged lines are in ``lines``.
            line_end=first.line,
            details=details,
            reason="string built by += in a loop; use a buffer / join for linear cost",
        )


BIOMARKER = StringConcatInLoopDetector()
