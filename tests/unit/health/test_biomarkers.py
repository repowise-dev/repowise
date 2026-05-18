"""Unit tests for the v1 biomarker detectors."""

from __future__ import annotations

from repowise.core.analysis.health.biomarkers import FileContext, detect_all
from repowise.core.analysis.health.biomarkers.brain_method import BrainMethodDetector
from repowise.core.analysis.health.biomarkers.complex_method import ComplexMethodDetector
from repowise.core.analysis.health.biomarkers.nested_complexity import (
    NestedComplexityDetector,
)
from repowise.core.analysis.health.complexity import FunctionComplexity


def _ctx(fns: list[FunctionComplexity], *, dependents: int = 0) -> FileContext:
    return FileContext(
        file_path="src/example.py",
        language="python",
        nloc=sum(f.nloc for f in fns),
        has_test_file=False,
        module=None,
        function_metrics={f.name: f for f in fns},
        git_meta={},
        dependents_count=dependents,
        pagerank_score=0.0,
    )


def test_complex_method_flags_high_ccn():
    fn = FunctionComplexity("biggie", 1, 80, ccn=20, max_nesting=2, cognitive=30, nloc=50)
    results = ComplexMethodDetector().detect(_ctx([fn]))
    assert len(results) == 1
    assert results[0].severity in ("medium", "high", "critical")
    assert results[0].details["ccn"] == 20


def test_complex_method_skips_low_ccn():
    fn = FunctionComplexity("tiny", 1, 5, ccn=3, max_nesting=1, cognitive=2, nloc=4)
    assert ComplexMethodDetector().detect(_ctx([fn])) == []


def test_nested_complexity_flags_deep_function():
    fn = FunctionComplexity("spiral", 1, 60, ccn=6, max_nesting=6, cognitive=20, nloc=40)
    results = NestedComplexityDetector().detect(_ctx([fn]))
    assert len(results) == 1
    assert results[0].severity in ("high", "critical")


def test_nested_complexity_skips_shallow():
    fn = FunctionComplexity("flat", 1, 3, ccn=2, max_nesting=1, cognitive=1, nloc=3)
    assert NestedComplexityDetector().detect(_ctx([fn])) == []


def test_brain_method_requires_centrality():
    fn = FunctionComplexity("lonely_brain", 1, 200, ccn=20, max_nesting=4, cognitive=40, nloc=150)
    # Too few dependents → not a brain method.
    assert BrainMethodDetector().detect(_ctx([fn], dependents=2)) == []
    # Hub file → flagged.
    results = BrainMethodDetector().detect(_ctx([fn], dependents=12))
    assert len(results) == 1
    assert results[0].details["dependents_count"] == 12


def test_detect_all_runs_every_biomarker():
    fn = FunctionComplexity("monster", 1, 200, ccn=20, max_nesting=6, cognitive=60, nloc=150)
    ctx = _ctx([fn], dependents=15)
    results = detect_all(ctx)
    types = {r.biomarker_type for r in results}
    # All three v1 biomarkers should fire on this constructed monster.
    assert "complex_method" in types
    assert "nested_complexity" in types
    assert "brain_method" in types
