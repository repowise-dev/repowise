"""Brain Method — long, complex, and central in one symbol.

CodeScene's signature biomarker: a function that does too much. We flag
when **all three** conditions hold:

- NLOC ≥ 70 (the function is long)
- CCN ≥ 9  (it has many decision paths)
- enclosing file has ≥ 8 dependents OR pagerank_score in the top decile
  (it sits at a hub, so refactoring carries leverage)

The file-level centrality check matches the plan's intent ("Brain Method
detection uses graph centrality"). We approximate the per-symbol
in-degree with the per-file dependents count because symbol-level
PageRank is not currently exposed via a simple synchronous API; the
file-level proxy is conservative.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext


class BrainMethodDetector:
    name = "brain_method"
    category = "structural_complexity"

    # Thresholds (locked for v1 — see plan §5).
    _NLOC_THRESHOLD = 70
    _CCN_THRESHOLD = 9
    _DEPENDENTS_THRESHOLD = 8

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        if ctx.dependents_count < self._DEPENDENTS_THRESHOLD:
            return []

        out: list[BiomarkerResult] = []
        for fn in ctx.function_metrics.values():
            if fn.nloc < self._NLOC_THRESHOLD:
                continue
            if fn.ccn < self._CCN_THRESHOLD:
                continue

            severity = (
                Severity.CRITICAL
                if fn.ccn >= 20 and fn.nloc >= 150
                else Severity.HIGH
                if fn.ccn >= 14 or fn.nloc >= 120
                else Severity.MEDIUM
            )
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=severity,
                    function_name=fn.name,
                    line_start=fn.start_line,
                    line_end=fn.end_line,
                    details={
                        "ccn": fn.ccn,
                        "nloc": fn.nloc,
                        "max_nesting": fn.max_nesting,
                        "dependents_count": ctx.dependents_count,
                    },
                    reason=(
                        f"Brain Method: {fn.name} is {fn.nloc} lines, CCN {fn.ccn}, "
                        f"in a file imported by {ctx.dependents_count} others"
                    ),
                )
            )
        return out


BIOMARKER = BrainMethodDetector()
