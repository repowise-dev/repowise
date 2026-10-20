"""Cross-language tests for the ``complex_conditional`` biomarker."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.complex_conditional import (
    ComplexConditionalDetector,
)
from repowise.core.analysis.health.complexity import walk_file_complexity
from repowise.core.analysis.health.models import Severity

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "lang_samples"


def _walk(rel_path: str, language: str):
    p = FIXTURES / rel_path
    if not p.exists():
        pytest.skip(f"fixture missing: {p}")
    results = walk_file_complexity(str(p), language, p.read_bytes())
    if not results:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    return results


def _ctx(metrics):
    return FileContext(
        file_path="x",
        language="python",
        nloc=10,
        has_test_file=False,
        module=None,
        all_functions=tuple(metrics),
    )


def _find(results, name):
    for r in results:
        if r.name == name:
            return r
    return None


def _assert_three_six_two(metrics, three: str, six: str, two: str):
    findings = ComplexConditionalDetector().detect(_ctx(metrics))
    by_fn = {f.function_name: f for f in findings}

    # 3-op → flagged at LOW
    assert three in by_fn, f"expected finding on {three} (3-op condition)"
    assert by_fn[three].severity == Severity.LOW
    assert by_fn[three].details["operator_count"] == 3

    # 6-op flat ``&&`` chain → flagged, but held at LOW
    assert six in by_fn, f"expected finding on {six} (6-op condition)"
    assert by_fn[six].severity == Severity.LOW
    assert by_fn[six].details["operator_count"] == 6
    assert by_fn[six].details["flat_chain"] is True

    # 2-op → not flagged
    assert two not in by_fn, f"{two} (2-op) should not flag"


def test_python_conditions():
    metrics = _walk("python/conditionals.py", "python")
    _assert_three_six_two(metrics, "three_ops", "six_ops", "two_ops")


def test_typescript_conditions():
    metrics = _walk("typescript/conditionals.ts", "typescript")
    _assert_three_six_two(metrics, "threeOps", "sixOps", "twoOps")


def test_go_conditions():
    metrics = _walk("go/conditionals.go", "go")
    _assert_three_six_two(metrics, "ThreeOps", "SixOps", "TwoOps")


def test_java_conditions():
    metrics = _walk("java/Conditionals.java", "java")
    _assert_three_six_two(metrics, "threeOps", "sixOps", "twoOps")


def test_rust_conditions():
    metrics = _walk("rust/conditionals.rs", "rust")
    _assert_three_six_two(metrics, "three_ops", "six_ops", "two_ops")


def test_walker_emits_complex_conditions_field():
    """Lock the new walker contract — assertion lives here so the
    cross-language fixtures double as walker coverage."""
    metrics = _walk("python/conditionals.py", "python")
    fc = _find(metrics, "six_ops")
    assert fc is not None
    assert len(fc.complex_conditions) == 1
    cond = fc.complex_conditions[0]
    assert cond.operator_count == 6
    assert cond.enclosing_construct == "while"
    # Existing CCN field must still be populated (regression guard).
    assert fc.ccn >= 7  # entry + while + 6 booleans


def _conditions(source: str, language: str, name: str):
    results = walk_file_complexity(f"x.{language}", language, source.encode())
    if not results:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    fc = _find(results, name)
    assert fc is not None
    return fc


@pytest.mark.parametrize(
    ("condition", "count", "flat"),
    [
        ("a === 1 || b === 2 || c === 3 || d === 4 || e === 5 || g === 6 || h === 7", 6, True),
        ("a && b && c && d && e && g && h", 6, True),
        ("a && b || c && d || e && g || h", 6, False),
        ("a || b || c || !d || e || g || h", 6, False),
        ("a !== b || c != d || e || g || h || i || j!", 6, True),
    ],
)
def test_flat_chain_typescript(condition: str, count: int, flat: bool):
    src = f"function f(a, b, c, d, e, g, h, i, j) {{ if ({condition}) {{ return 1; }} return 0; }}"
    cond = _conditions(src, "typescript", "f").complex_conditions[0]
    assert cond.operator_count == count
    assert cond.flat_chain is flat


@pytest.mark.parametrize(
    ("condition", "flat"),
    [
        ("a or b or c or d", True),
        ("a and b and c is not None and d not in e", True),
        ("a and b or c and d", False),
        ("a and not b and c and d", False),
    ],
)
def test_flat_chain_python(condition: str, flat: bool):
    src = f"def f(a, b, c, d, e):\n    if {condition}:\n        return 1\n    return 0\n"
    cond = _conditions(src, "python", "f").complex_conditions[0]
    assert cond.operator_count == 3
    assert cond.flat_chain is flat


def test_flat_chain_holds_low_and_mixed_keeps_count_severity():
    src = (
        "function flat(a, b, c, d, e, g, h) { if (a || b || c || d || e || g || h) { return 1; } }\n"
        "function knot(a, b, c, d, e, g, h) { if (a && b || c && !d || e && g || h) { return 1; } }\n"
        "function mixedLow(a, b, c, d) { if (a && b || c && d) { return 1; } }\n"
    )
    results = walk_file_complexity("x.ts", "typescript", src.encode())
    if not results:
        pytest.skip("tree-sitter language pack missing for typescript")
    by_fn = {f.function_name: f for f in ComplexConditionalDetector().detect(_ctx(results))}
    assert by_fn["flat"].severity == Severity.LOW
    assert by_fn["flat"].details["operator_count"] == 6
    assert by_fn["knot"].severity == Severity.CRITICAL
    assert by_fn["knot"].details["flat_chain"] is False
    assert by_fn["mixedLow"].severity == Severity.LOW
