"""Tests for Phase-3 structural / size biomarkers."""

from __future__ import annotations

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.bumpy_road import BumpyRoadDetector
from repowise.core.analysis.health.biomarkers.large_method import LargeMethodDetector
from repowise.core.analysis.health.biomarkers.primitive_obsession import (
    PrimitiveObsessionDetector,
)
from repowise.core.analysis.health.complexity import FunctionComplexity


def _ctx(fns: list[FunctionComplexity]) -> FileContext:
    return FileContext(
        file_path="src/example.py",
        language="python",
        nloc=sum(f.nloc for f in fns),
        has_test_file=False,
        module=None,
        function_metrics={f.name: f for f in fns},
        git_meta={},
        dependents_count=0,
        pagerank_score=0.0,
    )


# ---- bumpy_road ----------------------------------------------------------


def test_bumpy_road_flags_multi_hump_function():
    fn = FunctionComplexity("rough", 1, 80, ccn=8, max_nesting=3, cognitive=15, nloc=50, bumps=4)
    results = BumpyRoadDetector().detect(_ctx([fn]))
    assert len(results) == 1
    assert results[0].details["bumps"] == 4


def test_bumpy_road_skips_flat_function():
    fn = FunctionComplexity("flat", 1, 10, ccn=3, max_nesting=1, cognitive=2, nloc=8, bumps=0)
    assert BumpyRoadDetector().detect(_ctx([fn])) == []


def test_bumpy_road_requires_ccn_floor():
    # Lots of bumps but very low CCN — could happen on synthetic inputs.
    fn = FunctionComplexity("toy", 1, 20, ccn=2, max_nesting=2, cognitive=2, nloc=18, bumps=4)
    assert BumpyRoadDetector().detect(_ctx([fn])) == []


# ---- large_method --------------------------------------------------------


def test_large_method_severity_grades():
    small = FunctionComplexity("small", 1, 50, ccn=3, max_nesting=1, cognitive=4, nloc=40)
    big = FunctionComplexity("big", 1, 150, ccn=5, max_nesting=2, cognitive=10, nloc=130)
    huge = FunctionComplexity("huge", 1, 300, ccn=10, max_nesting=3, cognitive=20, nloc=250)
    d = LargeMethodDetector()
    assert d.detect(_ctx([small])) == []
    big_res = d.detect(_ctx([big]))
    assert len(big_res) == 1
    assert big_res[0].severity == "high"
    huge_res = d.detect(_ctx([huge]))
    assert huge_res[0].severity == "critical"


# ---- primitive_obsession -------------------------------------------------


# A filler function that lifts the enclosing file above the _MIN_FILE_NLOC floor
# so primitive_obsession is evaluated (a wide signature in a tiny module is
# idiomatic and suppressed — see test_primitive_obsession_skips_tiny_files).
_FILLER = FunctionComplexity(
    "filler", 1, 70, ccn=1, max_nesting=0, cognitive=0, nloc=70, param_count=0
)


def test_primitive_obsession_flags_wide_signature():
    fn = FunctionComplexity(
        "do_things", 1, 30, ccn=1, max_nesting=0, cognitive=0, nloc=24, param_count=7
    )
    out = PrimitiveObsessionDetector().detect(_ctx([fn, _FILLER]))
    assert len(out) == 1
    assert out[0].details["param_count"] == 7


def test_primitive_obsession_grace_for_constructors():
    # 6 params on a regular fn fires; on __init__ it doesn't (grace = 2).
    regular = FunctionComplexity(
        "build", 1, 20, ccn=1, max_nesting=0, cognitive=0, nloc=14, param_count=6
    )
    ctor = FunctionComplexity(
        "__init__", 1, 20, ccn=1, max_nesting=0, cognitive=0, nloc=14, param_count=6
    )
    d = PrimitiveObsessionDetector()
    assert d.detect(_ctx([regular, _FILLER]))
    assert d.detect(_ctx([ctor, _FILLER])) == []


def test_primitive_obsession_ignores_small_signatures():
    fn = FunctionComplexity(
        "narrow", 1, 20, ccn=1, max_nesting=0, cognitive=0, nloc=14, param_count=3
    )
    assert PrimitiveObsessionDetector().detect(_ctx([fn, _FILLER])) == []


def test_primitive_obsession_skips_tiny_files():
    # A wide signature in a module below the file-NLOC floor is idiomatic
    # (config/builder/forwarder), not a design smell — suppressed. (Phase-9
    # failure-forensics: anti-predictive on the small-file size band.)
    fn = FunctionComplexity(
        "configure", 1, 12, ccn=1, max_nesting=0, cognitive=0, nloc=10, param_count=8
    )
    assert PrimitiveObsessionDetector().detect(_ctx([fn])) == []
    # The same wide signature in a substantial module still fires.
    assert PrimitiveObsessionDetector().detect(_ctx([fn, _FILLER]))
