"""Unit tests for the ``function_hotspot`` biomarker."""

from __future__ import annotations

from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.biomarkers.function_hotspot import (
    FunctionHotspotDetector,
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
        function_metrics={fc.name: fc},
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
