"""Function Hotspot — functions that are both complex and frequently modified.

File-level hotspot misses the case where one file has a stable scaffold
plus a single hot, ugly function. Joining the per-line blame index
(``ctx.blame_index``) with the walker's ``FunctionComplexity`` lets us
project commit shas onto each function's line range and pair churn with
structural complexity.

Tier-aware: ``ctx.blame_index`` is ``None`` on the ESSENTIAL git tier
(per-line blame is deferred to ``backfill_blame()``); in that case the
detector emits zero findings. The same no-op holds when the engine
could not compute the repo-wide p80 (no functions had a non-zero
mod_count).

Severity — combined evidence:

* MEDIUM   when the function is at-or-above p80 churn AND meets the
  structural floor (``ccn >= 10`` or ``max_nesting >= 3``).
* HIGH     when *both* axes are top-decile (mod_count >= 2 * p80 OR ccn >= 15).
* CRITICAL when both axes are top-5% (mod_count >= 3 * p80 AND ccn >= 20).

The relative cut-offs are a calibrated approximation of the Z-score
criterion from the spec — easier to reason about, no per-repo
distribution math, and they collapse to the same result on the synthetic
fixtures we test against.
"""

from __future__ import annotations

from ....ingestion.git_indexer.function_blame import distinct_commits_in_range
from ..models import Severity
from .base import BiomarkerResult, FileContext

_CCN_FLOOR = 10
_NESTING_FLOOR = 3
_CCN_HIGH = 15
_CCN_CRITICAL = 20


def _severity_for(mod_count: int, p80: int, ccn: int) -> Severity:
    if p80 <= 0:
        return Severity.MEDIUM
    if mod_count >= 3 * p80 and ccn >= _CCN_CRITICAL:
        return Severity.CRITICAL
    if mod_count >= 2 * p80 or ccn >= _CCN_HIGH:
        return Severity.HIGH
    return Severity.MEDIUM


class FunctionHotspotDetector:
    name = "function_hotspot"
    category = "organizational"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        idx = ctx.blame_index
        if idx is None or not idx.lines:
            return []
        p80 = ctx.repo_function_mod_p80
        if p80 is None or p80 <= 0:
            return []

        findings: list[BiomarkerResult] = []
        for fn_name, fc in ctx.function_metrics.items():
            mod_count = len(distinct_commits_in_range(idx, fc.start_line, fc.end_line))
            if mod_count < p80:
                continue
            if fc.ccn < _CCN_FLOOR and fc.max_nesting < _NESTING_FLOOR:
                continue
            severity = _severity_for(mod_count, p80, fc.ccn)
            findings.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=severity,
                    function_name=fn_name,
                    line_start=fc.start_line,
                    line_end=fc.end_line,
                    details={
                        "modification_count": mod_count,
                        "repo_p80": p80,
                        "ccn": fc.ccn,
                        "max_nesting": fc.max_nesting,
                    },
                    reason=(
                        f"{fn_name} has been modified across {mod_count} "
                        f"commits (repo p80={p80}) and carries CCN={fc.ccn} / "
                        f"nesting={fc.max_nesting}"
                    ),
                )
            )
        return findings


BIOMARKER = FunctionHotspotDetector()
