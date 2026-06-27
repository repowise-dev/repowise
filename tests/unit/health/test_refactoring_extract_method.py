"""Unit tests for the Extract Method slicer + detector.

Best-effort: skip when the Python tree-sitter pack is missing.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

import pytest

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import analyze_file, find_extractions
from repowise.core.analysis.health.refactoring import detect_refactorings
from repowise.core.analysis.health.refactoring.extract_method import ExtractMethodDetector
from repowise.core.analysis.health.refactoring.models import RefactoringContext

# A long function whose tail (compute-average loop) is a clean extraction.
_PROCESS = """
def process(records, threshold):
    results = []
    errors = 0
    for r in records:
        if r is None:
            errors += 1
            continue
        results.append(r)
    total = 0
    count = 0
    for v in results:
        if v > threshold:
            total += v
            count += 1
        else:
            total -= v
    average = total / count if count else 0
    return average, errors
"""


@dataclass
class _Finding:
    biomarker_type: str
    function_name: str
    line_start: int
    health_impact: float


def _require_python() -> None:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip("tree-sitter language pack missing for python")
    if _get_language("python") is None:
        pytest.skip("tree-sitter language pack missing for python")


def _analyses(src: str):
    _require_python()
    res = analyze_file("m.py", "python", textwrap.dedent(src).encode(), flagged_only=False)
    if res.stats.functions_seen == 0:
        pytest.skip("tree-sitter language pack missing for python")
    return res.functions


def _first(src: str):
    fns = _analyses(src)
    assert fns
    return fns[0]


# -- slicer ---------------------------------------------------------------------


def test_finds_clean_extraction_with_inferred_signature():
    lmap = get_language_map("python")
    extractions = find_extractions(_first(_PROCESS), lmap)
    assert extractions, "expected at least one extraction"
    best = extractions[0]
    # The strongest extraction is the compute-average tail.
    assert "average" in best.returns
    assert "results" in best.params and "threshold" in best.params
    # Single clean return, bounded params.
    assert len(best.returns) <= 1
    assert best.ccn_removed >= 1
    assert best.slice_nloc >= 6


def test_no_extraction_when_every_span_has_a_jump():
    # A guard-clause cascade: every span contains a return, so nothing is a
    # single-exit slice -> no candidate.
    lmap = get_language_map("python")
    src = """
        def classify(x):
            if x < 0:
                return "neg"
            if x == 0:
                return "zero"
            if x < 10:
                return "small"
            if x < 100:
                return "medium"
            return "large"
        """
    extractions = find_extractions(_first(src), lmap)
    assert extractions == []


def test_whole_function_body_is_not_extracted():
    lmap = get_language_map("python")
    src = """
        def f(a, b):
            x = a + b
            y = x * 2
            z = y - a
            return z
        """
    # The only span covering the whole body is excluded; the remaining spans
    # carry no decision point, so nothing qualifies.
    assert find_extractions(_first(src), lmap) == []


def test_extractions_are_deterministic():
    lmap = get_language_map("python")
    fn = _first(_PROCESS)

    def serialize():
        return [
            (e.start_line, e.end_line, e.params, e.returns, e.ccn_removed)
            for e in find_extractions(fn, lmap)
        ]

    first = serialize()
    for _ in range(3):
        assert serialize() == first


# -- detector -------------------------------------------------------------------


def _ctx(src: str, findings):
    return RefactoringContext(
        file_path="m.py",
        language="python",
        nloc=100,
        findings=findings,
        function_analyses=_analyses(src),
    )


def test_detector_emits_suggestion_for_flagged_function():
    findings = [_Finding("complex_method", "process", line_start=2, health_impact=1.5)]
    suggestions = ExtractMethodDetector().detect(_ctx(_PROCESS, findings))
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s.refactoring_type == "extract_method"
    assert s.target_symbol == "process"
    assert s.source_biomarker == "complex_method"
    assert s.impact_delta == 1.5
    # Plan shape is the locked schema.
    assert set(s.plan) == {"span", "params", "returns", "suggested_name"}
    assert set(s.plan["span"]) == {"start", "end"}
    assert set(s.evidence) == {"slice_nloc", "ccn_removed"}
    assert s.blast_radius == {"callers_count": 0}
    assert s.confidence in ("medium", "high")


def test_detector_silent_without_matching_finding():
    # The function is analysed but no method biomarker fired -> no suggestion.
    suggestions = ExtractMethodDetector().detect(_ctx(_PROCESS, findings=[]))
    assert suggestions == []


def test_detector_silent_without_analyses():
    ctx = RefactoringContext(
        file_path="m.py",
        language="python",
        nloc=100,
        findings=[_Finding("complex_method", "process", 2, 1.0)],
        function_analyses=[],
    )
    assert ExtractMethodDetector().detect(ctx) == []


def test_detector_registered_in_registry():
    # The detector self-registers, so the generic runner picks it up.
    findings = [_Finding("large_method", "process", line_start=2, health_impact=2.0)]
    suggestions = detect_refactorings(_ctx(_PROCESS, findings))
    assert any(s.refactoring_type == "extract_method" for s in suggestions)


def test_detector_is_deterministic():
    findings = [_Finding("complex_method", "process", 2, 1.5)]

    def run():
        s = ExtractMethodDetector().detect(_ctx(_PROCESS, findings))[0]
        return (s.target_symbol, s.plan["span"], tuple(s.plan["params"]), tuple(s.plan["returns"]))

    first = run()
    for _ in range(3):
        assert run() == first
