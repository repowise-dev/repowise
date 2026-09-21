"""Untested Hotspot — a churning, central file with no test coverage.

Fires when a file is **all three** of:

- a *hotspot* (``git_meta['is_hotspot']``, which is top-quartile churn with
  absolute activity floors under it)
- under-tested (``line_coverage_pct`` < 40 when coverage is available,
  OR, when it isn't, neither a paired test file nor a test reaching it
  through the call graph). A file a report measured and found nothing
  coverable in is neither: it has no code to test, so it is skipped
  outright rather than sent down the no-data fallback.
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

from ....test_paths import is_test_related_path
from ..models import Severity
from .base import BiomarkerResult, FileContext

_COVERAGE_LOW = 40.0
_COVERAGE_VERY_LOW = 15.0
_DEPENDENTS_THRESHOLD = 4
def _is_hotspot(ctx: FileContext) -> bool:
    """The repository's own hotspot verdict, and only that.

    This used to widen it with two absolute alternatives -- eight commits in 90
    days, or a decayed churn score over 0.8 -- either of which a busy repository
    clears for a large share of its files while a quiet one never clears at all.
    ``is_hotspot`` is already top-quartile churn with activity floors under it,
    so the alternatives only ever made the gate less repo-relative, never more
    informative.
    """
    return bool((ctx.git_meta or {}).get("is_hotspot"))


class UntestedHotspotDetector:
    name = "untested_hotspot"
    category = "test_coverage"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        # A test file is not undertested. Coverage reports rarely instrument
        # the suite itself, so where coverage is ingested every test file reads
        # as 0% covered and a churning one would be accused of it.
        if is_test_related_path(ctx.file_path, ctx.language):
            return []
        if not _is_hotspot(ctx):
            return []
        if ctx.dependents_count < _DEPENDENTS_THRESHOLD:
            # Not enough leverage to flag — would over-trigger on
            # leaf files churned by one author.
            return []

        cov = ctx.line_coverage_pct
        if cov is None and ctx.coverage_measured and ctx.total_coverable_lines == 0:
            # Measured, and there was nothing to measure: a type-only module, a
            # barrel of re-exports. The fallback below is for files whose
            # coverage is *unknown*, and applying it here would accuse a file
            # with no executable line in it of being untested — which is what
            # issue #2193 saw, at critical severity, on a barrel of 28 type
            # declarations with 21 dependents.
            return []

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
