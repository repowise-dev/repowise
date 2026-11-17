"""Unit tests for the ``function_hotspot`` biomarker."""

from __future__ import annotations

from repowise.core.analysis.health.biomarkers.base import BiomarkerResult, FileContext
from repowise.core.analysis.health.biomarkers.function_hotspot import (
    FunctionHotspotDetector,
    refresh_replayed,
)
from repowise.core.analysis.health.complexity import FunctionComplexity
from repowise.core.analysis.health.models import Severity
from repowise.core.ingestion.git_indexer.function_blame import BlameIndex


def _blame(ranges: dict[tuple[int, int], list[str]]) -> BlameIndex:
    """Build a BlameIndex where each line in (start,end) maps to the
    first sha in the list — total distinct shas across that range
    equals ``len(shas)``."""
    lines: dict[int, tuple[str, int]] = {}
    for (start, end), shas in ranges.items():
        # spread shas across the range
        per = max(1, (end - start + 1) // len(shas))
        ln = start
        for sha in shas:
            for _ in range(per):
                if ln > end:
                    break
                lines[ln] = (sha, 1700000000)
                ln += 1
        # fill any remainder with the last sha
        while ln <= end:
            lines[ln] = (shas[-1], 1700000000)
            ln += 1
    return BlameIndex(lines=lines)


def _fc(name: str, start: int, end: int, ccn: int = 1, nesting: int = 0) -> FunctionComplexity:
    return FunctionComplexity(
        name=name,
        start_line=start,
        end_line=end,
        ccn=ccn,
        max_nesting=nesting,
        cognitive=0,
        nloc=end - start + 1,
    )


def _ctx(
    fc: FunctionComplexity,
    blame: BlameIndex | None,
    p80: int | None,
) -> FileContext:
    return FileContext(
        file_path="src/foo.py",
        language="python",
        nloc=20,
        has_test_file=False,
        module=None,
        all_functions=(fc,),
        blame_index=blame,
        repo_function_mod_p80=p80,
    )


def test_positive_high_churn_high_ccn():
    fc = _fc("hot", 1, 10, ccn=12, nesting=3)
    blame = _blame({(1, 10): [f"sha{i:040d}" for i in range(8)]})
    findings = FunctionHotspotDetector().detect(_ctx(fc, blame, p80=5))
    assert len(findings) == 1
    assert findings[0].biomarker_type == "function_hotspot"
    assert findings[0].details["modification_count"] >= 5
    assert findings[0].severity in {Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL}


def test_negative_high_churn_low_ccn():
    fc = _fc("dull", 1, 10, ccn=3, nesting=1)
    blame = _blame({(1, 10): [f"sha{i:040d}" for i in range(8)]})
    assert FunctionHotspotDetector().detect(_ctx(fc, blame, p80=5)) == []


def test_negative_high_ccn_low_churn():
    fc = _fc("hot", 1, 10, ccn=30, nesting=4)
    blame = _blame({(1, 10): ["sha0000000000000000000000000000000000000a"]})
    # mod_count == 1, below p80 of 5
    assert FunctionHotspotDetector().detect(_ctx(fc, blame, p80=5)) == []


def test_noop_when_blame_index_missing():
    fc = _fc("hot", 1, 10, ccn=20, nesting=5)
    # ESSENTIAL tier: no blame index → zero findings
    assert FunctionHotspotDetector().detect(_ctx(fc, None, p80=5)) == []


def test_noop_when_p80_missing():
    fc = _fc("hot", 1, 10, ccn=20, nesting=5)
    blame = _blame({(1, 10): [f"sha{i:040d}" for i in range(20)]})
    assert FunctionHotspotDetector().detect(_ctx(fc, blame, p80=None)) == []


def test_severity_escalates_on_extreme_combined_axes():
    fc = _fc("nightmare", 1, 30, ccn=25, nesting=6)
    blame = _blame({(1, 30): [f"sha{i:040d}" for i in range(20)]})
    findings = FunctionHotspotDetector().detect(_ctx(fc, blame, p80=5))
    assert len(findings) == 1
    # mod_count >= 3*p80 (15) and ccn >= 20 → CRITICAL
    assert findings[0].severity == Severity.CRITICAL


def _changed(n: int) -> BlameIndex:
    return _blame({(1, 30): [f"sha{i:040d}" for i in range(n)]})


def test_a_thin_window_needs_five_changes():
    # A shallow clone's p80 is 1: two changes used to read as HIGH. A function
    # changed often inside the window still is a hotspot.
    fc = _fc("hot", 1, 30, ccn=25, nesting=6)
    assert FunctionHotspotDetector().detect(_ctx(fc, _changed(2), p80=1)) == []
    found = FunctionHotspotDetector().detect(_ctx(fc, _changed(20), p80=1))
    assert [f.severity for f in found] == [Severity.CRITICAL]


def test_four_changes_never_make_a_hotspot():
    fc = _fc("busy", 1, 30, ccn=12, nesting=3)
    assert FunctionHotspotDetector().detect(_ctx(fc, _changed(4), p80=3)) == []
    found = FunctionHotspotDetector().detect(_ctx(fc, _changed(5), p80=3))
    assert [f.severity for f in found] == [Severity.MEDIUM]


def test_severity_scales_from_the_floor_not_a_small_p80():
    # 2 * p80 would be HIGH at 6 changes; the bar is 5, so HIGH needs 10.
    fc = _fc("busy", 1, 30, ccn=12, nesting=3)
    assert FunctionHotspotDetector().detect(_ctx(fc, _changed(6), p80=3))[0].severity == (
        Severity.MEDIUM
    )
    assert FunctionHotspotDetector().detect(_ctx(fc, _changed(10), p80=3))[0].severity == (
        Severity.HIGH
    )


def test_refresh_replayed_updates_complexity_or_drops_hotspot():
    def replayed() -> BiomarkerResult:
        return BiomarkerResult(
            biomarker_type="function_hotspot",
            severity=Severity.MEDIUM,
            function_name="hot",
            line_start=1,
            line_end=10,
            details={"ccn": 9, "max_nesting": 7, "modification_count": 12, "repo_p80": 5},
        )

    kept = replayed()
    assert refresh_replayed(kept, [_fc("hot", 1, 10, ccn=12, nesting=5)])
    assert (kept.details["ccn"], kept.details["max_nesting"]) == (12, 5)
    assert kept.severity == Severity.HIGH
    assert "CCN=12 / nesting=5" in kept.reason
    assert not refresh_replayed(replayed(), [_fc("hot", 1, 10, ccn=9, nesting=2)])
    assert not refresh_replayed(replayed(), [_fc("other", 1, 10, ccn=30)])


def test_refresh_replayed_picks_same_named_function_by_line_start():
    replayed = BiomarkerResult(
        biomarker_type="function_hotspot",
        severity=Severity.MEDIUM,
        function_name="run",
        line_start=42,
        line_end=60,
        details={"ccn": 12, "max_nesting": 3, "modification_count": 6, "repo_p80": 5},
    )
    # A simple ``run`` earlier in the file must not stand in for the hot one,
    # which has since moved down two lines.
    functions = [_fc("run", 5, 8, ccn=1), _fc("run", 44, 62, ccn=13, nesting=4)]
    assert refresh_replayed(replayed, functions)
    assert replayed.details["ccn"] == 13
    assert (replayed.line_start, replayed.line_end) == (44, 62)


def test_refresh_replayed_never_lands_two_rows_on_one_function():
    def replayed(line: int) -> BiomarkerResult:
        return BiomarkerResult(
            biomarker_type="function_hotspot",
            severity=Severity.MEDIUM,
            function_name="run",
            line_start=line,
            line_end=line + 10,
            details={"ccn": 12, "max_nesting": 3, "modification_count": 6, "repo_p80": 5},
        )

    # One of the two ``run`` twins was deleted; only the surviving one matches.
    functions = [_fc("run", 44, 62, ccn=13, nesting=4)]
    claimed: set[int] = set()
    assert refresh_replayed(replayed(42), functions, claimed)
    assert not refresh_replayed(replayed(5), functions, claimed)
