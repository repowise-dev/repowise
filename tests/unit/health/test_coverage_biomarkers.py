"""Unit tests for the Phase 2 coverage-aware biomarkers."""

from __future__ import annotations

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.coverage_gap import CoverageGapDetector
from repowise.core.analysis.health.biomarkers.coverage_gradient import (
    CoverageGradientDetector,
)
from repowise.core.analysis.health.biomarkers.untested_hotspot import (
    UntestedHotspotDetector,
)


def _ctx(
    *,
    path: str = "src/example.py",
    dependents: int = 0,
    has_test_file: bool = False,
    git_meta: dict | None = None,
    line_cov: float | None = None,
    branch_cov: float | None = None,
    covered_lines: set[int] | None = None,
    total_lines: int = 0,
) -> FileContext:
    return FileContext(
        file_path=path,
        language="python",
        nloc=100,
        has_test_file=has_test_file,
        module=None,
        git_meta=git_meta or {},
        dependents_count=dependents,
        line_coverage_pct=line_cov,
        branch_coverage_pct=branch_cov,
        covered_lines=covered_lines or set(),
        total_coverable_lines=total_lines,
    )


# ---------------------------------------------------------------------------
# untested_hotspot
# ---------------------------------------------------------------------------


def test_untested_hotspot_fires_when_low_coverage_and_hotspot() -> None:
    ctx = _ctx(
        git_meta={"is_hotspot": True},
        dependents=10,
        line_cov=12.0,
        total_lines=50,
    )
    results = UntestedHotspotDetector().detect(ctx)
    assert len(results) == 1
    assert results[0].severity == "critical"


def test_untested_hotspot_silent_when_not_hotspot() -> None:
    ctx = _ctx(git_meta={"is_hotspot": False}, dependents=20, line_cov=5.0, total_lines=100)
    assert UntestedHotspotDetector().detect(ctx) == []


def test_untested_hotspot_silent_when_dependents_low() -> None:
    ctx = _ctx(git_meta={"is_hotspot": True}, dependents=1, line_cov=5.0, total_lines=100)
    assert UntestedHotspotDetector().detect(ctx) == []


def test_untested_hotspot_silent_when_well_covered() -> None:
    ctx = _ctx(git_meta={"is_hotspot": True}, dependents=10, line_cov=85.0, total_lines=100)
    assert UntestedHotspotDetector().detect(ctx) == []


def test_untested_hotspot_falls_back_when_no_coverage_data() -> None:
    ctx = _ctx(
        git_meta={"commit_count_90d": 12},
        dependents=6,
        has_test_file=False,
    )
    results = UntestedHotspotDetector().detect(ctx)
    assert len(results) == 1
    assert "no coverage data" in results[0].reason


def test_untested_hotspot_skips_when_paired_test_present() -> None:
    ctx = _ctx(
        git_meta={"commit_count_90d": 12},
        dependents=6,
        has_test_file=True,
    )
    assert UntestedHotspotDetector().detect(ctx) == []


# ---------------------------------------------------------------------------
# coverage_gap
# ---------------------------------------------------------------------------


def test_coverage_gap_fires_on_regular_gap() -> None:
    ctx = _ctx(line_cov=50.0, total_lines=100, covered_lines=set(range(1, 51)))
    results = CoverageGapDetector().detect(ctx)
    assert len(results) == 1
    assert results[0].severity == "low"
    assert results[0].details["uncovered_lines"] == 50


def test_coverage_gap_fires_on_deep_gap() -> None:
    ctx = _ctx(line_cov=20.0, total_lines=200, covered_lines=set(range(1, 41)))
    results = CoverageGapDetector().detect(ctx)
    assert len(results) == 1
    assert results[0].severity == "high"


def test_coverage_gap_skips_test_files() -> None:
    ctx = _ctx(
        path="tests/test_thing.py",
        line_cov=10.0,
        total_lines=200,
    )
    assert CoverageGapDetector().detect(ctx) == []


def test_coverage_gap_skips_when_no_coverage_data() -> None:
    ctx = _ctx(line_cov=None, total_lines=0)
    assert CoverageGapDetector().detect(ctx) == []


def test_coverage_gap_skips_well_covered() -> None:
    ctx = _ctx(line_cov=85.0, total_lines=200, covered_lines=set(range(1, 171)))
    assert CoverageGapDetector().detect(ctx) == []


# ---------------------------------------------------------------------------
# coverage_gradient
# ---------------------------------------------------------------------------


def test_coverage_gradient_fires_proportionally_to_uncovered() -> None:
    # 75% covered → 25% uncovered → deduction 4.0 * 0.25 = 1.0.
    ctx = _ctx(line_cov=75.0, total_lines=100)
    results = CoverageGradientDetector().detect(ctx)
    assert len(results) == 1
    r = results[0]
    assert r.deduction == 1.0
    assert r.details["uncovered_fraction"] == 0.25
    assert r.severity == "low"


def test_coverage_gradient_fires_on_well_covered_file() -> None:
    # The binary gates stay silent at 92% — the gradient still fires (the point
    # of this biomarker): 8% uncovered → deduction 0.32.
    ctx = _ctx(line_cov=92.0, total_lines=300)
    results = CoverageGradientDetector().detect(ctx)
    assert len(results) == 1
    assert round(results[0].deduction, 4) == 0.32
    assert CoverageGapDetector().detect(ctx) == []


def test_coverage_gradient_severity_bands() -> None:
    assert CoverageGradientDetector().detect(_ctx(line_cov=30.0))[0].severity == "high"
    assert CoverageGradientDetector().detect(_ctx(line_cov=55.0))[0].severity == "medium"
    assert CoverageGradientDetector().detect(_ctx(line_cov=90.0))[0].severity == "low"


def test_coverage_gradient_silent_when_no_coverage_data() -> None:
    # Absent coverage is never imputed as uncovered.
    assert CoverageGradientDetector().detect(_ctx(line_cov=None)) == []


def test_coverage_gradient_silent_at_full_coverage() -> None:
    assert CoverageGradientDetector().detect(_ctx(line_cov=100.0)) == []


def test_coverage_gradient_skips_test_files() -> None:
    ctx = _ctx(path="tests/test_thing.py", line_cov=10.0)
    assert CoverageGradientDetector().detect(ctx) == []
