"""Brain Method — long, complex, and central in one symbol.

A function that does too much. We flag when **all three** conditions
hold:

- NLOC ≥ 70 (the function is long)
- CCN ≥ 9  (it has many decision paths)
- the enclosing file is *central* — see the centrality gate below.

**Centrality gate (language-agnostic).** A fixed ``dependents ≥ 8`` gate
is calibrated for Python's dense import graph and never fires on
sparse-graph languages (TypeScript barrels, Rust's lower in-degrees),
silently dropping genuinely complex+central functions. Instead we gate on
``dependents ≥ floor`` where

    floor = min(8, max(repo_dependents_p80, 3))

i.e. a file qualifies if it has ≥ 8 dependents (the absolute hub bar) OR
sits in the repo's top quintile of connected files (``repo_dependents_p80``,
computed by the engine), with a small floor of 3 so a repo where p80 is
tiny doesn't flag every file with a single importer. When the engine
can't supply a percentile (no graph), ``floor`` stays at 8 — identical to
the original behaviour. We use file-level in-degree as a conservative
proxy for symbol centrality (symbol-level PageRank isn't exposed via a
simple synchronous API).
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
    # Lower bound on the percentile branch so a sparse repo (small p80)
    # doesn't reduce the centrality bar to "any file with one importer".
    _CENTRALITY_MIN_FLOOR = 3

    def _centrality_floor(self, ctx: FileContext) -> int:
        p80 = ctx.repo_dependents_p80
        if p80 is None:
            return self._DEPENDENTS_THRESHOLD
        return min(self._DEPENDENTS_THRESHOLD, max(p80, self._CENTRALITY_MIN_FLOOR))

    @classmethod
    def severity_for(cls, ccn: int, nloc: int) -> Severity | None:
        """The severity a function of this shape earns in a file that already
        clears the centrality gate, ``None`` below the size/complexity bar."""
        if nloc < cls._NLOC_THRESHOLD or ccn < cls._CCN_THRESHOLD:
            return None
        return (
            Severity.CRITICAL
            if ccn >= 20 and nloc >= 150
            else Severity.HIGH
            if ccn >= 14 or nloc >= 120
            else Severity.MEDIUM
        )

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        floor = self._centrality_floor(ctx)
        if ctx.dependents_count < floor:
            return []

        out: list[BiomarkerResult] = []
        for fn in ctx.all_functions:
            severity = self.severity_for(fn.ccn, fn.nloc)
            if severity is None:
                continue
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
                        "centrality_floor": floor,
                    },
                    reason=(
                        f"Brain Method: {fn.name} is {fn.nloc} lines, CCN {fn.ccn}, "
                        f"in a file imported by {ctx.dependents_count} others"
                    ),
                )
            )
        return out


BIOMARKER = BrainMethodDetector()
