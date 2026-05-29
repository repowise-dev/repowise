"""Tests for the test-quality biomarkers + large_method size hardening.

- ``large_assertion_block`` / ``duplicated_assertion_block`` fire only on
  test files and live in the mild ``test_quality`` category.
- ``large_method`` now requires a minimal CCN floor so a long-but-flat
  body (a big data literal) no longer reads as a complexity smell.
"""

from __future__ import annotations

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.duplicated_assertion_block import (
    DuplicatedAssertionBlockDetector,
)
from repowise.core.analysis.health.biomarkers.large_assertion_block import (
    LargeAssertionBlockDetector,
)
from repowise.core.analysis.health.biomarkers.large_method import LargeMethodDetector
from repowise.core.analysis.health.complexity import FunctionComplexity
from repowise.core.analysis.health.duplication import ClonePair
from repowise.core.analysis.health.models import Severity


def _fn(
    name: str,
    *,
    nloc: int = 5,
    ccn: int = 1,
    assertion_blocks: list[tuple[int, int, int]] | None = None,
) -> FunctionComplexity:
    return FunctionComplexity(
        name=name,
        start_line=1,
        end_line=1 + nloc,
        ccn=ccn,
        max_nesting=0,
        cognitive=0,
        nloc=nloc,
        assertion_blocks=assertion_blocks or [],
    )


def _ctx(
    *,
    file_path: str,
    functions: list[FunctionComplexity],
    clones: list[ClonePair] | None = None,
) -> FileContext:
    return FileContext(
        file_path=file_path,
        language="python",
        nloc=200,
        has_test_file=False,
        module=None,
        function_metrics={fn.name: fn for fn in functions},
        clones=clones or [],
    )


# ---- large_assertion_block -----------------------------------------------


def test_large_assertion_block_fires_on_test_file():
    fn = _fn("test_x", assertion_blocks=[(10, 30, 18)])
    out = LargeAssertionBlockDetector().detect(_ctx(file_path="tests/test_x.py", functions=[fn]))
    assert len(out) == 1
    assert out[0].details["assertion_count"] == 18
    assert out[0].severity == Severity.MEDIUM  # 15 <= count < 30


def test_large_assertion_block_high_severity():
    fn = _fn("test_x", assertion_blocks=[(10, 60, 35)])
    out = LargeAssertionBlockDetector().detect(_ctx(file_path="tests/test_x.py", functions=[fn]))
    assert out[0].severity == Severity.HIGH  # count >= 30


def test_large_assertion_block_ignores_small_runs():
    fn = _fn("test_x", assertion_blocks=[(10, 18, 8)])
    assert (
        LargeAssertionBlockDetector().detect(_ctx(file_path="tests/test_x.py", functions=[fn]))
        == []
    )


def test_large_assertion_block_silent_on_production_file():
    # Same big block, but the file is not a test → never fires.
    fn = _fn("validate", assertion_blocks=[(10, 30, 18)])
    assert (
        LargeAssertionBlockDetector().detect(_ctx(file_path="src/validate.py", functions=[fn]))
        == []
    )


# ---- duplicated_assertion_block ------------------------------------------


def _clone(path: str, a: tuple[int, int], partner: str, b: tuple[int, int]) -> ClonePair:
    return ClonePair(
        file_a=path,
        file_b=partner,
        a_start_line=a[0],
        a_end_line=a[1],
        b_start_line=b[0],
        b_end_line=b[1],
        token_count=80,
    )


def test_duplicated_assertion_block_fires_when_clone_overlaps_block():
    fn = _fn("test_x", assertion_blocks=[(10, 20, 6)])
    clone = _clone("tests/test_x.py", (12, 19), "tests/test_y.py", (5, 12))
    out = DuplicatedAssertionBlockDetector().detect(
        _ctx(file_path="tests/test_x.py", functions=[fn], clones=[clone])
    )
    assert len(out) == 1
    assert out[0].severity == Severity.MEDIUM
    assert out[0].details["partner_file"] == "tests/test_y.py"


def test_duplicated_assertion_block_ignores_clone_outside_block():
    fn = _fn("test_x", assertion_blocks=[(10, 20, 6)])
    clone = _clone("tests/test_x.py", (40, 48), "tests/test_y.py", (5, 12))
    assert (
        DuplicatedAssertionBlockDetector().detect(
            _ctx(file_path="tests/test_x.py", functions=[fn], clones=[clone])
        )
        == []
    )


def test_duplicated_assertion_block_silent_on_production_file():
    fn = _fn("run", assertion_blocks=[(10, 20, 6)])
    clone = _clone("src/run.py", (12, 19), "src/other.py", (5, 12))
    assert (
        DuplicatedAssertionBlockDetector().detect(
            _ctx(file_path="src/run.py", functions=[fn], clones=[clone])
        )
        == []
    )


# ---- large_method size hardening -----------------------------------------


def test_large_method_skips_long_flat_body():
    # 150 lines but zero branching (a big config dict) → not a smell.
    flat = _fn("CONFIG", nloc=150, ccn=1)
    assert LargeMethodDetector().detect(_ctx(file_path="src/x.py", functions=[flat])) == []


def test_large_method_fires_with_any_branching():
    branchy = _fn("process", nloc=150, ccn=2)
    out = LargeMethodDetector().detect(_ctx(file_path="src/x.py", functions=[branchy]))
    assert len(out) == 1
    assert out[0].details["nloc"] == 150
