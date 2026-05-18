"""Coverage Gap — significant uncovered surface in a non-trivial file.

Fires when a file has coverage data and:

- line_coverage_pct < 60 **and** uncovered lines ≥ 25, OR
- line_coverage_pct < 30 **and** total_coverable_lines ≥ 50.

Test files themselves are exempt (we don't care that
``test_foo.py`` has no coverage). Without coverage data the biomarker
does nothing — the absence-of-coverage case is the
``untested_hotspot`` biomarker's territory.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_LINE_COVERAGE_GAP = 60.0
_LINE_COVERAGE_DEEP_GAP = 30.0
_MIN_UNCOVERED_LINES = 25
_MIN_FILE_SIZE = 50


class CoverageGapDetector:
    name = "coverage_gap"
    category = "test_coverage"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        if ctx.line_coverage_pct is None:
            return []
        if ctx.total_coverable_lines <= 0:
            return []
        if _looks_like_test_path(ctx.file_path):
            return []

        cov = ctx.line_coverage_pct
        total = ctx.total_coverable_lines
        uncovered = total - len(ctx.covered_lines or ())
        # Some parsers don't emit a per-line set; fall back to math.
        if uncovered <= 0:
            uncovered = round(total * (100.0 - cov) / 100.0)

        deep_gap = cov < _LINE_COVERAGE_DEEP_GAP and total >= _MIN_FILE_SIZE
        regular_gap = cov < _LINE_COVERAGE_GAP and uncovered >= _MIN_UNCOVERED_LINES
        if not (deep_gap or regular_gap):
            return []

        if deep_gap and uncovered >= 100:
            severity = Severity.HIGH
        elif deep_gap:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

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
                    "uncovered_lines": uncovered,
                    "total_coverable_lines": total,
                },
                reason=(
                    f"{uncovered}/{total} lines uncovered ({cov:.0f}% line coverage)"
                ),
            )
        ]


def _looks_like_test_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    return (
        "/test/" in p
        or "/tests/" in p
        or "/__tests__/" in p
        or p.startswith(("test/", "tests/", "__tests__/"))
        or p.endswith(("_test.py", "_test.go", ".test.ts", ".test.tsx", ".test.js",
                       ".spec.ts", ".spec.js"))
        or p.rsplit("/", 1)[-1].startswith("test_")
    )


BIOMARKER = CoverageGapDetector()
