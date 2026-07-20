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


# -- prefix-sum equivalence -------------------------------------------------------
#
# find_extractions computes span metrics via per-statement prefix sums. This
# reference reimplements the original per-span _span_metrics enumeration; the
# two must produce identical candidate lists on any input.


def _find_extractions_reference(analysis, lmap):
    from repowise.core.analysis.health.dataflow.slice import (
        _MAX_CANDIDATES,
        _MAX_PARAMS,
        _MAX_RETURNS,
        _MIN_CCN_REMOVED,
        _MIN_SLICE_NLOC,
        _MIN_STMTS,
        Extraction,
        _all_blocks,
        _infer_in_out,
        _sorted,
        _span_metrics,
        _unwrap_container,
        _var_lines,
    )

    fn_node = analysis.fn_node
    if fn_node is None:
        return []
    body = fn_node.child_by_field_name("body")
    if body is None:
        return []
    body_container = _unwrap_container(body, lmap.block_kinds)
    def_lines, use_lines = _var_lines(analysis.def_use)
    decision_kinds = (
        lmap.branch_kinds
        | lmap.loop_kinds
        | lmap.case_kinds
        | lmap.catch_kinds
        | lmap.boolean_operator_kinds
    )
    jump_kinds = lmap.return_kinds | lmap.raise_kinds | lmap.break_kinds | lmap.continue_kinds
    scope_kinds = lmap.function_kinds | lmap.lambda_kinds
    tail_stmt_kinds = (
        lmap.statement_wrapper_kinds | lmap.local_decl_kinds
        if lmap.statement_wrapper_kinds
        else None
    )

    out = []
    evaluated = 0
    for block in _all_blocks(fn_node, lmap.block_kinds, scope_kinds):
        stmts = block.named_children
        n = len(stmts)
        is_body = block.id == body_container.id
        for i in range(n):
            for j in range(i, n):
                evaluated += 1
                if evaluated > _MAX_CANDIDATES:
                    return _sorted(out)
                length = j - i + 1
                if length < _MIN_STMTS:
                    continue
                if is_body and length == n:
                    continue
                if (
                    tail_stmt_kinds is not None
                    and j == n - 1
                    and stmts[j].type not in tail_stmt_kinds
                ):
                    continue
                span = stmts[i : j + 1]
                decisions, has_jump = _span_metrics(span, decision_kinds, jump_kinds, scope_kinds)
                if has_jump or decisions < _MIN_CCN_REMOVED:
                    continue
                slice_nloc = sum(st.end_point[0] - st.start_point[0] + 1 for st in span)
                if slice_nloc < _MIN_SLICE_NLOC:
                    continue
                s = span[0].start_point[0] + 1
                e = span[-1].end_point[0] + 1
                params, returns = _infer_in_out(def_lines, use_lines, s, e)
                if len(params) > _MAX_PARAMS or len(returns) > _MAX_RETURNS:
                    continue
                out.append(
                    Extraction(
                        start_line=s,
                        end_line=e,
                        params=params,
                        returns=returns,
                        slice_nloc=slice_nloc,
                        ccn_removed=decisions,
                    )
                )
    return _sorted(out)


_NESTED = """
def transform(items, flags):
    out = []
    for it in items:
        if it in flags:
            for k in range(3):
                if k and it:
                    out.append((it, k))
        else:
            while it > 0:
                it -= 1
                out.append(it)
    def helper(v):
        if v:
            return -v
        return v
    total = 0
    for o in out:
        total += helper(o if isinstance(o, int) else o[1])
    if total > 10 or len(out) > 5:
        total = total // 2
    return total
"""

_JUMPY = """
def scan(rows):
    hits = 0
    for r in rows:
        if r is None:
            continue
        if r < 0:
            break
        hits += 1
    try:
        rate = hits / len(rows)
    except ZeroDivisionError:
        rate = 0.0
    if rate > 0.5:
        hits += 1
    return hits, rate
"""


@pytest.mark.parametrize("src", [_PROCESS, _NESTED, _JUMPY])
def test_prefix_sum_matches_per_span_reference(src):
    lmap = get_language_map("python")
    fn = _first(src)
    assert find_extractions(fn, lmap) == _find_extractions_reference(fn, lmap)


def test_prefix_sum_matches_per_span_reference_go():
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip("tree-sitter language pack missing")
    if _get_language("go") is None:
        pytest.skip("tree-sitter language pack missing for go")
    src = textwrap.dedent(
        """
        package main

        func Transform(items []int, limit int) int {
            total := 0
            count := 0
            for _, it := range items {
                if it > limit {
                    total += it
                    count++
                } else {
                    total -= it
                }
            }
            avg := 0
            if count > 0 {
                avg = total / count
            }
            for i := 0; i < avg; i++ {
                if i%2 == 0 {
                    total += i
                }
            }
            return total
        }
        """
    )
    res = analyze_file("m.go", "go", src.encode(), flagged_only=False)
    if res.stats.functions_seen == 0:
        pytest.skip("go parse unavailable")
    lmap = get_language_map("go")
    for fn in res.functions:
        assert find_extractions(fn, lmap) == _find_extractions_reference(fn, lmap)


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
