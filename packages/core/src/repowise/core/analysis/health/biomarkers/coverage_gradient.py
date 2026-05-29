"""Coverage Gradient - a continuous, per-file test-coverage deduction.

The two binary coverage biomarkers (``untested_hotspot`` / ``coverage_gap``)
only fire below hard thresholds (~40-60% line coverage), so on well-tested
codebases - where most files sit at 85-99% - the score is effectively blind to
coverage even though the *uncovered fraction* still carries real defect signal.

This biomarker makes the coverage signal **continuous and monotonic**: for any
file with KNOWN line coverage it deducts health in proportion to the uncovered
fraction. The deduction is a plain, fully attributable arithmetic function of
one number (``1 - line_coverage_pct/100``), so it stays linear / explainable and
adds zero walk cost (it reads already-parsed coverage).

- Deduction (health points) = ``_WEIGHT x uncovered_fraction`` - clamped by the
  ``test_coverage_gradient`` category cap, which binds once a file is ≥50%
  uncovered. ``_WEIGHT`` was calibrated offline against the defect corpus.
- **Absent coverage ≠ zero coverage.** When no coverage report was ingested
  (``line_coverage_pct is None``) the biomarker is silent - it never imputes
  uncovered for missing data.
- Test files are exempt (we don't penalise ``test_foo.py`` for being uncovered),
  matching ``coverage_gap``.

It is intentionally distinct from the binary gates and lives in its own capped
category so the additive continuous signal neither squeezes nor is squeezed by
the has-tests / hotspot gates.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

# Calibrated offline (2026-05-30) on the 13-repo defect corpus: a per-file
# deduction of 4.0 x uncovered_fraction recovers +0.043 corpus AUC
# [95% CI +0.023, +0.061] on the covered subset (~65% of the continuous-feature
# ceiling), Popt-neutral. Reproduced by
# repowise-bench/health-defect/coverage_scoring_experiment.py (w=4, cap=2.0).
_WEIGHT = 4.0

# Display-only severity bands (the deduction comes from the continuous
# ``deduction`` override, not the severity table).
_COVERAGE_HIGH = 40.0
_COVERAGE_MEDIUM = 70.0


def _looks_like_test_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    return (
        "/test/" in p
        or "/tests/" in p
        or "/__tests__/" in p
        or p.startswith(("test/", "tests/", "__tests__/"))
        or p.endswith(
            ("_test.py", "_test.go", ".test.ts", ".test.tsx", ".test.js", ".spec.ts", ".spec.js")
        )
        or p.rsplit("/", 1)[-1].startswith("test_")
    )


class CoverageGradientDetector:
    name = "coverage_gradient"
    category = "test_coverage_gradient"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        cov = ctx.line_coverage_pct
        if cov is None:
            # No coverage data -> silent. Absent is not the same as uncovered.
            return []
        if _looks_like_test_path(ctx.file_path):
            return []

        uncovered_fraction = max(0.0, (100.0 - float(cov)) / 100.0)
        if uncovered_fraction <= 0.0:
            return []

        if cov < _COVERAGE_HIGH:
            severity = Severity.HIGH
        elif cov < _COVERAGE_MEDIUM:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

        deduction = _WEIGHT * uncovered_fraction
        uncovered_pct = round(uncovered_fraction * 100.0)
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
                    "uncovered_fraction": round(uncovered_fraction, 4),
                },
                reason=(
                    f"{uncovered_pct}% of lines uncovered ({cov:.0f}% line coverage) - "
                    f"uncovered code carries proportionally more defect risk"
                ),
                deduction=deduction,
            )
        ]


BIOMARKER = CoverageGradientDetector()
