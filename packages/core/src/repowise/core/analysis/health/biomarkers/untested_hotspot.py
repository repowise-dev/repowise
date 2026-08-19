"""Untested Hotspot — a churning, central file with no test coverage.

Fires when a file is **all three** of:

- a *hotspot* (``git_meta['is_hotspot']`` true OR commit_count_90d ≥ 8)
- under-tested (``line_coverage_pct`` < 40 when coverage is available,
  OR, when it isn't, neither a paired test file nor a test reaching it
  through the call graph)
- centrally depended on (``dependents_count`` ≥ 4 OR temporal_hotspot
  in the top decile)

Severity grades on how bad the coverage is and how many dependents the
file has.

When no coverage report has been ingested the biomarker falls back to two
signals that both mean "something tests this": the paired-test-file naming
convention, and ``reached_by_tests``, a test file whose calls reach this one in
the dependency graph. Either suppresses the finding. Both over-claim relative to
real execution, which is the right direction of error for a check whose output
is an accusation.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_COVERAGE_LOW = 40.0
_COVERAGE_VERY_LOW = 15.0
_DEPENDENTS_THRESHOLD = 4
_COMMITS_90D_THRESHOLD = 8


def _is_hotspot(ctx: FileContext) -> bool:
    meta = ctx.git_meta or {}
    if bool(meta.get("is_hotspot")):
        return True
    try:
        if int(meta.get("commit_count_90d", 0) or 0) >= _COMMITS_90D_THRESHOLD:
            return True
    except (TypeError, ValueError):
        pass
    try:
        if float(meta.get("temporal_hotspot_score", 0.0) or 0.0) >= 0.8:
            return True
    except (TypeError, ValueError):
        pass
    return False


class UntestedHotspotDetector:
    name = "untested_hotspot"
    category = "test_coverage"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        if not _is_hotspot(ctx):
            return []
        if ctx.dependents_count < _DEPENDENTS_THRESHOLD:
            # Not enough leverage to flag — would over-trigger on
            # leaf files churned by one author.
            return []

        cov = ctx.line_coverage_pct
        if cov is None:
            # Fallback: no coverage data. Flag only when nothing says a test
            # touches this file. Two independent things can say so, and either
            # is enough. This is the one place the biomarker is asserting a
            # negative, so the bar for asserting it is "no signal at all".
            #
            # ``reached_by_tests`` is the graph one, and it is what stops the
            # long-standing false positive: a suite that names its tests for
            # behaviour rather than for the file under test satisfies no naming
            # convention, so ``has_test_file`` was False and this fired on files
            # the graph records several test files importing.
            if ctx.has_test_file or ctx.reached_by_tests:
                return []
            cov_for_severity = 0.0
            reason = (
                f"Hotspot with no paired test file and no coverage data — "
                f"{ctx.dependents_count} dependents"
            )
        else:
            if cov >= _COVERAGE_LOW:
                return []
            cov_for_severity = cov
            reason = f"Hotspot with {cov:.0f}% line coverage and {ctx.dependents_count} dependents"

        if cov_for_severity <= _COVERAGE_VERY_LOW and ctx.dependents_count >= 10:
            severity = Severity.CRITICAL
        elif cov_for_severity <= _COVERAGE_VERY_LOW or ctx.dependents_count >= 10:
            severity = Severity.HIGH
        else:
            severity = Severity.MEDIUM

        meta = ctx.git_meta or {}
        return [
            BiomarkerResult(
                biomarker_type=self.name,
                severity=severity,
                function_name=None,
                line_start=None,
                line_end=None,
                details={
                    "line_coverage_pct": cov,
                    "branch_coverage_pct": ctx.branch_coverage_pct,
                    "dependents_count": ctx.dependents_count,
                    "commit_count_90d": meta.get("commit_count_90d"),
                    "has_test_file": ctx.has_test_file,
                    "reached_by_tests": ctx.reached_by_tests,
                },
                reason=reason,
            )
        ]


BIOMARKER = UntestedHotspotDetector()
