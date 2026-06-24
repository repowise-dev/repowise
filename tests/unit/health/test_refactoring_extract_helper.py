"""Tests for the Extract Helper refactoring detector (clone dedup).

The detector turns the verified clone pairs the health pass already computed
(``ClonePair`` records, surfaced on ``RefactoringContext.clones``) into a
structured "extract this duplicated block into a shared helper" suggestion.

Fixtures build ``ClonePair`` records directly so the clone geometry — which
files, which line ranges, co-change — is explicit and the expected
occurrences are unambiguous and deterministic.
"""

from __future__ import annotations

from repowise.core.analysis.health.duplication import ClonePair
from repowise.core.analysis.health.refactoring import (
    RefactoringContext,
    detect_refactorings,
    registered_detectors,
)
from repowise.core.analysis.health.refactoring.extract_helper import (
    _common_directory,
    _merge_ranges_per_file,
)


class _DryFinding:
    """Minimal HealthFindingData-like stand-in for a dry_violation finding."""

    def __init__(self, start: int, end: int, impact: float):
        self.biomarker_type = "dry_violation"
        self.line_start = start
        self.line_end = end
        self.health_impact = impact
        self.function_name = None
        self.details = {}


def _pair(
    file_a: str,
    file_b: str,
    a_start: int,
    a_end: int,
    b_start: int,
    b_end: int,
    *,
    token_count: int = 50,
    co_change: int = 0,
) -> ClonePair:
    return ClonePair(
        file_a=file_a,
        file_b=file_b,
        a_start_line=a_start,
        a_end_line=a_end,
        b_start_line=b_start,
        b_end_line=b_end,
        token_count=token_count,
        co_change_count=co_change,
    )


def _ctx(
    file_path: str,
    clones: list[ClonePair],
    *,
    findings: list | None = None,
    module_map: dict[str, str] | None = None,
) -> RefactoringContext:
    return RefactoringContext(
        file_path=file_path,
        language="python",
        nloc=200,
        classes=[],
        findings=findings or [],
        dependents_count=0,
        clones=clones,
        module_map=module_map or {},
    )


# ---- registration --------------------------------------------------------


def test_detector_registered():
    assert "extract_helper" in [d.name for d in registered_detectors()]


def test_silent_without_clones():
    assert detect_refactorings(_ctx("a/x.py", [])) == []


# ---- cross-file extraction + canonical anchor ----------------------------


def test_emits_for_cross_file_clone():
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55)
    sugs = [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    ]
    assert len(sugs) == 1
    s = sugs[0]
    assert s.file_path == "pkg/a.py"
    assert s.target_symbol == "a.py:10-25"
    assert s.plan["occurrences"] == [
        {"file": "pkg/a.py", "line_start": 10, "line_end": 25},
        {"file": "pkg/b.py", "line_start": 40, "line_end": 55},
    ]
    assert s.evidence["occurrence_count"] == 2
    assert s.evidence["duplicated_lines"] == 16
    assert s.evidence["is_intra_file"] is False
    assert s.blast_radius == {
        "files": ["pkg/b.py"],
        "file_count": 1,
        "co_change_count": 0,
    }


def test_canonical_anchor_dedups():
    # The same pair seen from the non-anchor file (b > a) yields nothing, so a
    # clone set is suggested exactly once.
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55)
    sugs = [
        s
        for s in detect_refactorings(_ctx("pkg/b.py", [pair]))
        if s.refactoring_type == "extract_helper"
    ]
    assert sugs == []


def test_transitive_clone_set_one_suggestion():
    # A clones with B and C; from A (the min path) both partners are visible →
    # one suggestion listing all three sites.
    clones = [
        _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55),
        _pair("pkg/a.py", "pkg/c.py", 10, 25, 70, 85),
    ]
    sugs = [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", clones))
        if s.refactoring_type == "extract_helper"
    ]
    assert len(sugs) == 1
    files = [o["file"] for o in sugs[0].plan["occurrences"]]
    assert files == ["pkg/a.py", "pkg/b.py", "pkg/c.py"]
    assert sugs[0].blast_radius["file_count"] == 2


def test_intra_file_clone():
    pair = _pair("pkg/a.py", "pkg/a.py", 10, 25, 60, 75)
    sugs = [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    ]
    assert len(sugs) == 1
    s = sugs[0]
    assert s.evidence["is_intra_file"] is True
    assert s.plan["occurrences"] == [
        {"file": "pkg/a.py", "line_start": 10, "line_end": 25},
        {"file": "pkg/a.py", "line_start": 60, "line_end": 75},
    ]
    assert s.blast_radius["files"] == []


def test_overlapping_windows_collapse_to_one_site():
    # The clone detector emits a block as several offset windows; they must
    # coalesce into one occurrence per file, not read as many sites.
    clones = [
        _pair("pkg/a.py", "pkg/b.py", 8, 35, 40, 67),
        _pair("pkg/a.py", "pkg/b.py", 9, 36, 41, 68),
        _pair("pkg/a.py", "pkg/b.py", 22, 30, 54, 62),
    ]
    sugs = [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", clones))
        if s.refactoring_type == "extract_helper"
    ]
    assert len(sugs) == 1
    assert sugs[0].plan["occurrences"] == [
        {"file": "pkg/a.py", "line_start": 8, "line_end": 36},
        {"file": "pkg/b.py", "line_start": 40, "line_end": 68},
    ]
    assert sugs[0].evidence["occurrence_count"] == 2


def test_merge_ranges_per_file_helper():
    merged = _merge_ranges_per_file(
        [("a.py", 8, 35), ("a.py", 9, 36), ("a.py", 22, 28), ("a.py", 60, 75), ("b.py", 1, 9)]
    )
    assert merged == [("a.py", 8, 36), ("a.py", 60, 75), ("b.py", 1, 9)]


def test_two_distinct_blocks_in_one_file():
    # Two non-overlapping anchor regions → two separate suggestions.
    clones = [
        _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55),
        _pair("pkg/a.py", "pkg/c.py", 100, 120, 5, 25),
    ]
    sugs = [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", clones))
        if s.refactoring_type == "extract_helper"
    ]
    assert len(sugs) == 2
    assert {s.line_start for s in sugs} == {10, 100}


# ---- gates ---------------------------------------------------------------


def test_below_min_lines_skipped():
    # 6-line clone — below the 8-line helper floor.
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 15, 40, 45)
    assert [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    ] == []


def test_test_file_occurrences_dropped():
    # A clone shared only with a test file collapses to one real site → skip.
    pair = _pair("pkg/a.py", "tests/test_a.py", 10, 25, 40, 55)
    assert [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    ] == []


def test_test_file_dropped_but_real_sites_kept():
    clones = [
        _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55),
        _pair("pkg/a.py", "tests/test_a.py", 10, 25, 5, 20),
    ]
    sugs = [
        s
        for s in detect_refactorings(_ctx("pkg/a.py", clones))
        if s.refactoring_type == "extract_helper"
    ]
    assert len(sugs) == 1
    files = [o["file"] for o in sugs[0].plan["occurrences"]]
    assert files == ["pkg/a.py", "pkg/b.py"]


def test_generated_migration_occurrences_dropped():
    # Migration boilerplate duplicates heavily but is never refactored — a
    # clone confined to migration files yields no suggestion.
    pair = _pair(
        "core/alembic/versions/0001_a.py",
        "core/alembic/versions/0002_b.py",
        10,
        25,
        10,
        25,
    )
    assert [
        s
        for s in detect_refactorings(_ctx("core/alembic/versions/0001_a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    ] == []


def test_disabled_detector_yields_nothing():
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55)
    assert detect_refactorings(_ctx("pkg/a.py", [pair]), disabled=["extract_helper"]) == []


# ---- impact + confidence -------------------------------------------------


def test_impact_from_overlapping_dry_violation():
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55)
    findings = [_DryFinding(12, 22, 1.8)]
    sugs = detect_refactorings(_ctx("pkg/a.py", [pair], findings=findings))
    s = next(s for s in sugs if s.refactoring_type == "extract_helper")
    assert s.impact_delta == 1.8


def test_no_impact_when_finding_disjoint():
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55)
    findings = [_DryFinding(200, 210, 1.8)]
    sugs = detect_refactorings(_ctx("pkg/a.py", [pair], findings=findings))
    s = next(s for s in sugs if s.refactoring_type == "extract_helper")
    assert s.impact_delta == 0.0


def test_confidence_high_when_actively_co_changed():
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55, co_change=4)
    s = next(
        s
        for s in detect_refactorings(_ctx("pkg/a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    )
    assert s.confidence == "high"
    assert s.evidence["co_change_count"] == 4


def test_confidence_medium_when_dormant():
    pair = _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55, co_change=0)
    s = next(
        s
        for s in detect_refactorings(_ctx("pkg/a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    )
    assert s.confidence == "medium"


# ---- suggested site ------------------------------------------------------


def test_suggested_site_community_centroid():
    pair = _pair("api/a.py", "core/b.py", 10, 25, 40, 55)
    module_map = {"api/a.py": "api", "core/b.py": "api"}
    s = next(
        s
        for s in detect_refactorings(_ctx("api/a.py", [pair], module_map=module_map))
        if s.refactoring_type == "extract_helper"
    )
    assert s.plan["suggested_site"]["module"] == "api"


def test_suggested_site_directory_fallback():
    pair = _pair("pkg/sub/a.py", "pkg/sub/b.py", 10, 25, 40, 55)
    s = next(
        s
        for s in detect_refactorings(_ctx("pkg/sub/a.py", [pair]))
        if s.refactoring_type == "extract_helper"
    )
    assert s.plan["suggested_site"]["module"] is None
    assert s.plan["suggested_site"]["directory"] == "pkg/sub"


def test_common_directory_helper():
    assert _common_directory(["a/b/x.py", "a/b/y.py"]) == "a/b"
    assert _common_directory(["a/b/x.py", "a/c/y.py"]) == "a"
    assert _common_directory(["a/x.py", "b/y.py"]) is None
    assert _common_directory(["x.py", "a/y.py"]) is None


# ---- determinism ---------------------------------------------------------


def test_deterministic_and_stable_order():
    clones = [
        _pair("pkg/a.py", "pkg/b.py", 10, 25, 40, 55),
        _pair("pkg/a.py", "pkg/c.py", 100, 120, 5, 25),
    ]
    findings = [_DryFinding(100, 120, 3.0), _DryFinding(10, 25, 1.0)]
    a = detect_refactorings(_ctx("pkg/a.py", clones, findings=findings))
    b = detect_refactorings(_ctx("pkg/a.py", clones, findings=findings))
    a = [s for s in a if s.refactoring_type == "extract_helper"]
    b = [s for s in b if s.refactoring_type == "extract_helper"]
    # Bigger recovered impact first.
    assert [s.line_start for s in a] == [100, 10]
    assert [(s.target_symbol, s.plan) for s in a] == [(s.target_symbol, s.plan) for s in b]
