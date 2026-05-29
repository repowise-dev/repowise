"""Large Assertion Block — a single test packed with too many assertions.

A test function that fires 15+ assertions in one uninterrupted run is
usually testing several behaviours at once: when it fails it points at a
line, not a cause, and it's brittle to unrelated changes. Splitting it
into focused cases makes failures legible.

Fires **only on test files** (so production code that happens to use
``assert`` for invariants is never flagged) and only on a *consecutive*
run of assertions — the walker records these as ``assertion_blocks`` on
each ``FunctionComplexity``.
"""

from __future__ import annotations

from ..coverage import is_test_file
from ..models import Severity
from .base import BiomarkerResult, FileContext


class LargeAssertionBlockDetector:
    name = "large_assertion_block"
    category = "test_quality"

    _MIN_COUNT = 15

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        if not is_test_file(ctx.file_path):
            return []
        out: list[BiomarkerResult] = []
        for fn in ctx.function_metrics.values():
            for start, end, count in fn.assertion_blocks:
                if count < self._MIN_COUNT:
                    continue
                severity = Severity.HIGH if count >= 30 else Severity.MEDIUM
                out.append(
                    BiomarkerResult(
                        biomarker_type=self.name,
                        severity=severity,
                        function_name=fn.name,
                        line_start=start,
                        line_end=end,
                        details={"function": fn.name, "assertion_count": count},
                        reason=f"{fn.name} runs {count} assertions in one block",
                    )
                )
        return out


BIOMARKER = LargeAssertionBlockDetector()
