"""Untested Complexity — a function whose CRAP score reaches 30.

CRAP weighs a function's cyclomatic complexity by how little of it the tests
run (:mod:`..crap`). Coverage is measured from the file's coverage report when
its line sets can say; otherwise it is the function's tier (a test calls it,
tests reach it, or neither), and the finding says so and never quotes a
percentage. Severity: medium from 30, high from 50, critical from 100, the
upper bands for measured coverage only. A function with complexity of 30 or
more stays above the bar at full coverage, so its finding asks for a
refactor rather than tests. Test files are exempt.
"""

from __future__ import annotations

from ..complexity.dispatch import judged_ccn
from ..coverage_actions import coverage_actions
from ..crap import CRAP_THRESHOLD, coverage_tier, function_coverage, function_crap
from ..models import Severity
from .base import BiomarkerResult, FileContext

# Apex tests reach code through platform dispatch the graph does not see, so "no
# test found" is no evidence there. Lift when Apex test reach models dispatch.
_UNSEEN_TEST_REACH = frozenset({"apex"})

_HIGH = 50.0


def _unseen_reach(basis: str, tier: str, language: str) -> bool:
    return basis != "measured" and tier == "none" and language in _UNSEEN_TEST_REACH
_CRITICAL = 100.0

_TIER_TEXT = {
    "direct": "a test calls it",
    "reached": "tests reach it",
    "none": "no test found reaching it",
}


def _severity(crap: float, measured: bool) -> Severity:
    if measured and crap >= _CRITICAL:
        return Severity.CRITICAL
    if measured and crap >= _HIGH:
        return Severity.HIGH
    return Severity.MEDIUM


def _measured(ctx: FileContext) -> list[float | None]:
    """Each function's measured coverage, all ``None`` when the report cannot say."""
    functions = ctx.all_functions
    if ctx.line_coverage_pct is None:
        return [None] * len(functions)
    # A file the report ran none of: every function in it is at 0%.
    if ctx.line_coverage_pct == 0 and not ctx.covered_lines and ctx.total_coverable_lines > 0:
        return [0.0] * len(functions)
    return function_coverage(
        [(fn.start_line, fn.end_line) for fn in functions],
        ctx.covered_lines,
        ctx.coverable_lines or None,
    )


class UntestedComplexityDetector:
    name = "untested_complexity"
    category = "test_coverage"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        if ctx.is_test_material or ctx.reexport_only or not ctx.all_functions:
            return []
        out: list[BiomarkerResult] = []
        for fn, measured in zip(ctx.all_functions, _measured(ctx), strict=True):
            ccn = judged_ccn(fn)
            tier = coverage_tier(
                ctx.function_test_hops.get(fn.start_line),
                ctx.reached_by_tests or ctx.has_test_file,
            )
            result = function_crap(ccn, measured, tier)
            if result.crap < CRAP_THRESHOLD:
                continue
            if _unseen_reach(result.basis, tier, ctx.language):
                continue
            is_measured = result.basis == "measured"
            refactor = ccn >= CRAP_THRESHOLD
            details: dict = {
                "crap": round(result.crap, 1),
                "ccn": fn.ccn,
                "coverage_basis": result.basis,
                "action": "refactor" if refactor else "add_tests",
            }
            if ccn != fn.ccn:
                details["judged_ccn"] = ccn
            if is_measured:
                details["coverage_pct"] = result.coverage_pct
                covered = f"{result.coverage_pct:.0f}% of its lines covered"
            else:
                details["coverage_tier"] = result.tier
                covered = f"estimated, {_TIER_TEXT[tier]}"
            reason = f"{fn.name} has CRAP {result.crap:.0f} (complexity {ccn}, {covered})"
            if refactor:
                reason += "; full coverage would not bring it under 30, so simplify it"
            # ``actions`` carries the step ``action`` names, with its prompt.
            # Only on evidence of what runs: a measurement, or a test that calls
            # it. A graph that found no test is not a reason to act.
            if is_measured or tier == "direct":
                details["actions"] = coverage_actions(
                    self.name,
                    ctx.file_path,
                    function=fn.name,
                    target_test=ctx.function_test_files.get(fn.start_line) or ctx.file_test,
                    evidence=f"CRAP {result.crap:.0f} at complexity {ccn}, {covered}",
                    refactor=refactor,
                )
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=_severity(result.crap, is_measured),
                    function_name=fn.name,
                    line_start=fn.start_line,
                    line_end=fn.end_line,
                    details=details,
                    reason=reason,
                )
            )
        return out


BIOMARKER = UntestedComplexityDetector()
