"""Unit tests for the ``code_age_volatility`` biomarker."""

from __future__ import annotations

import time

from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.biomarkers.code_age_volatility import (
    CodeAgeVolatilityDetector,
)
from repowise.core.analysis.health.complexity import FunctionComplexity
from repowise.core.analysis.health.models import Severity
from repowise.core.ingestion.git_indexer.function_blame import BlameIndex

DAY = 86400


def _fc(name: str, start: int, end: int) -> FunctionComplexity:
    return FunctionComplexity(
        name=name,
        start_line=start,
        end_line=end,
        ccn=1,
        max_nesting=0,
        cognitive=0,
        nloc=end - start + 1,
    )


def _ctx(fc: FunctionComplexity, blame: BlameIndex | None) -> FileContext:
    return FileContext(
        file_path="src/foo.py",
        language="python",
        nloc=10,
        has_test_file=False,
        module=None,
        function_metrics={fc.name: fc},
        blame_index=blame,
    )


def _blame_for_age(
    fc: FunctionComplexity,
    *,
    old_lines: int,
    old_age_days: int,
    recent_shas: int,
    recent_age_days: int = 5,
) -> BlameIndex:
    """Construct a blame where most lines are old + a few lines are recent."""
    now = int(time.time())
    old_ts = now - old_age_days * DAY
    recent_ts = now - recent_age_days * DAY
    lines: dict[int, tuple[str, int]] = {}
    ln = fc.start_line
    for i in range(old_lines):
        lines[ln] = (f"a{i:039d}", old_ts)
        ln += 1
    for i in range(recent_shas):
        if ln > fc.end_line:
            break
        lines[ln] = (f"b{i:039d}", recent_ts)
        ln += 1
    return BlameIndex(lines=lines)


def test_positive_old_with_recent_edits():
    fc = _fc("legacy", 1, 10)
    blame = _blame_for_age(fc, old_lines=7, old_age_days=500, recent_shas=3)
    findings = CodeAgeVolatilityDetector().detect(_ctx(fc, blame))
    assert len(findings) == 1
    f = findings[0]
    assert f.biomarker_type == "code_age_volatility"
    assert f.details["median_age_days"] >= 365
    assert f.details["recent_mod_count"] >= 2


def test_negative_old_but_no_recent_edits():
    fc = _fc("ancient", 1, 10)
    # Every line is old; no recent shas.
    blame = _blame_for_age(fc, old_lines=10, old_age_days=900, recent_shas=0)
    assert CodeAgeVolatilityDetector().detect(_ctx(fc, blame)) == []


def test_negative_recent_and_young():
    fc = _fc("fresh", 1, 10)
    # All lines are recent — median age well under the 1-year floor.
    blame = _blame_for_age(fc, old_lines=0, old_age_days=0, recent_shas=10, recent_age_days=5)
    assert CodeAgeVolatilityDetector().detect(_ctx(fc, blame)) == []


def test_noop_when_blame_index_missing():
    fc = _fc("any", 1, 10)
    assert CodeAgeVolatilityDetector().detect(_ctx(fc, None)) == []
    assert CodeAgeVolatilityDetector().detect(_ctx(fc, BlameIndex())) == []


def test_critical_when_very_old_and_many_recent_mods():
    fc = _fc("nightmare", 1, 20)
    blame = _blame_for_age(fc, old_lines=14, old_age_days=900, recent_shas=6)
    findings = CodeAgeVolatilityDetector().detect(_ctx(fc, blame))
    assert len(findings) == 1
    assert findings[0].severity == Severity.CRITICAL
