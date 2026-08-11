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

from ....test_paths import is_test_related_path
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


class CoverageGradientDetector:
    name = "coverage_gradient"
    category = "test_coverage_gradient"
    # Declares what the ``deduction`` override below already makes true: this
    # fires on every file that has coverage data at all, so its magnitude ranks
    # files against each other but never answers "why this file". Readers that
    # pick one headline biomarker per file consult ``continuous_biomarkers()``
    # to prefer a discrete cause.
    continuous = True

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        cov = ctx.line_coverage_pct
        if cov is None:
            # No coverage data -> silent. Absent is not the same as uncovered.
            return []
        if is_test_related_path(ctx.file_path, ctx.language):
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
                    # The continuous base deduction (pre-weight, pre-cap). Stored
                    # so the score-breakdown API can show the gradient instead of
                    # recomputing a severity-band proxy.
                    "deduction": round(deduction, 4),
                },
                reason=(
                    f"{uncovered_pct}% of lines uncovered ({cov:.0f}% line coverage) - "
                    f"uncovered code carries proportionally more defect risk"
                ),
                deduction=deduction,
            )
        ]


BIOMARKER = CoverageGradientDetector()
