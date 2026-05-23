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
        function_metrics={fc.name: fc for fc in metrics},
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

    # 6-op → CRITICAL
    assert six in by_fn, f"expected finding on {six} (6-op condition)"
    assert by_fn[six].severity == Severity.CRITICAL
    assert by_fn[six].details["operator_count"] == 6

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
