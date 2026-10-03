"""DRY Violation biomarker tests."""

from __future__ import annotations

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.dry_violation import DryViolationDetector
from repowise.core.analysis.health.duplication import ClonePair


def _ctx(file_path: str, pairs: list[ClonePair], dup_pct: float | None) -> FileContext:
    return FileContext(
        file_path=file_path,
        language="python",
        nloc=200,
        has_test_file=False,
        module=None,
        git_meta={},
        dependents_count=0,
        pagerank_score=0.0,
        clones=pairs,
        duplication_pct=dup_pct,
    )


def _pair(a: str, b: str, *, lines: int = 12, co_change: int = 0) -> ClonePair:
    return ClonePair(
        file_a=a,
        file_b=b,
        a_start_line=10,
        a_end_line=10 + lines - 1,
        b_start_line=20,
        b_end_line=20 + lines - 1,
        token_count=80,
        co_change_count=co_change,
    )


def test_dry_violation_skips_when_no_clones():
    ctx = _ctx("a.py", [], None)
    assert DryViolationDetector().detect(ctx) == []


def test_dry_violation_skips_low_duplication():
    pair = _pair("a.py", "b.py", lines=10)
    ctx = _ctx("a.py", [pair], dup_pct=3.0)
    assert DryViolationDetector().detect(ctx) == []


def test_dry_violation_fires_on_significant_duplication():
    pair = _pair("a.py", "b.py", lines=20)
    ctx = _ctx("a.py", [pair], dup_pct=15.0)
    out = DryViolationDetector().detect(ctx)
    assert len(out) == 1
    assert out[0].details["worst_clone_partner"] == "b.py"
    assert out[0].details["clone_pair_count"] == 1
    # Inactive clone, mid-sized → low severity.
    assert out[0].severity == "low"


def test_dry_violation_promotes_severity_for_active_clones():
    pair = _pair("a.py", "b.py", lines=20, co_change=8)
    ctx = _ctx("a.py", [pair], dup_pct=30.0)
    out = DryViolationDetector().detect(ctx)
    assert out[0].severity == "high"
    assert out[0].details["worst_clone_co_change"] == 8


def test_dry_violation_picks_worst_clone():
    big_dormant = _pair("a.py", "b.py", lines=30, co_change=0)
    small_active = _pair("a.py", "c.py", lines=10, co_change=10)
    ctx = _ctx("a.py", [big_dormant, small_active], dup_pct=20.0)
    out = DryViolationDetector().detect(ctx)
    # Active clone wins regardless of size.
    assert out[0].details["worst_clone_partner"] == "c.py"


def test_dry_violation_ignores_a_file_cloned_only_with_itself():
    ctx = _ctx("a.py", [_pair("a.py", "a.py", lines=30)], dup_pct=60.0)
    assert DryViolationDetector().detect(ctx) == []


def test_dry_violation_grades_and_names_only_cross_file_clones():
    # Intra-file lines 10-39 and 20-49 plus a cross-file clone on 60-71: 52
    # covered lines at 52%, of which the cross-file clone holds 12.
    intra = _pair("a.py", "a.py", lines=30)
    cross = ClonePair("a.py", "b.py", 60, 71, 5, 16, token_count=80)
    out = DryViolationDetector().detect(_ctx("a.py", [intra, cross], dup_pct=52.0))
    assert len(out) == 1
    assert out[0].details["worst_clone_partner"] == "b.py"
    assert out[0].details["cross_file_duplication_pct"] == 12.0
    assert out[0].details["clone_pair_count"] == 1
    assert out[0].reason.startswith("12% of file duplicated in other files")
    # The same file below the floor once its own clones stop counting.
    assert DryViolationDetector().detect(_ctx("a.py", [intra, cross], dup_pct=30.0)) == []


def test_dry_violation_intra_clone_overlapping_a_cross_clone_counts_once():
    # Intra 10-29 / 40-59 and a cross-file clone on 20-39: 50 covered lines at
    # 50%, of which the cross-file clone covers 20.
    intra = ClonePair("a.py", "a.py", 10, 29, 40, 59, token_count=80)
    cross = ClonePair("a.py", "b.py", 20, 39, 5, 24, token_count=80)
    out = DryViolationDetector().detect(_ctx("a.py", [intra, cross], dup_pct=50.0))
    assert out[0].details["cross_file_duplication_pct"] == 20.0


def test_dry_violation_capped_file_pct_only_scales_down():
    # The stored % is capped at 100 even when covered lines pass NLOC; the
    # cross-file share scales that cap, so it can only come out lower.
    intra = ClonePair("a.py", "a.py", 1, 60, 61, 120, token_count=80)
    cross = ClonePair("a.py", "b.py", 1, 30, 1, 30, token_count=80)
    out = DryViolationDetector().detect(_ctx("a.py", [intra, cross], dup_pct=100.0))
    assert out[0].details["cross_file_duplication_pct"] == 25.0
