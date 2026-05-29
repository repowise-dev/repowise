"""Unit tests for the tree-sitter complexity walker.

These tests are best-effort: tree-sitter language packs may not all be
installed in CI. The walker returns ``[]`` when a language pack is
missing, so each assertion guards with ``pytest.skip`` rather than fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.health.complexity import walk_file, walk_file_complexity

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "lang_samples"


def _walk(rel_path: str, language: str):
    p = FIXTURES / rel_path
    if not p.exists():
        pytest.skip(f"fixture missing: {p}")
    results = walk_file_complexity(str(p), language, p.read_bytes())
    if not results:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    return results


def _walk_classes(rel_path: str, language: str):
    p = FIXTURES / rel_path
    if not p.exists():
        pytest.skip(f"fixture missing: {p}")
    fcx = walk_file(str(p), language, p.read_bytes())
    if not fcx.classes:
        pytest.skip(f"tree-sitter language pack missing / no classes for {language}")
    return {c.name: c for c in fcx.classes}


def _find(results, name):
    matches = [r for r in results if r.name == name]
    return matches[0] if matches else None


def test_python_nested_depth():
    results = _walk("python/nested.py", "python")
    deep = _find(results, "deeply_nested")
    shallow = _find(results, "shallow")
    assert deep is not None
    assert shallow is not None
    assert deep.max_nesting >= 4, f"expected ≥4 nesting, got {deep.max_nesting}"
    assert shallow.max_nesting == 0
    assert deep.ccn > shallow.ccn


def test_python_complex_method_ccn():
    results = _walk("python/complex.py", "python")
    many = _find(results, "many_branches")
    assert many is not None
    assert many.ccn >= 9, f"expected CCN ≥ 9, got {many.ccn}"


def test_typescript_nested_depth():
    results = _walk("typescript/nested.ts", "typescript")
    deep = _find(results, "deeplyNested")
    if deep is None:
        pytest.skip("typescript nested function not detected")
    assert deep.max_nesting >= 4


def test_go_nested_depth():
    results = _walk("go/nested.go", "go")
    deep = _find(results, "DeeplyNested")
    if deep is None:
        pytest.skip("go function not detected")
    assert deep.max_nesting >= 4


def test_javascript_nested_depth():
    results = _walk("javascript/nested.js", "javascript")
    deep = _find(results, "deeplyNested")
    if deep is None:
        pytest.skip("js function not detected")
    assert deep.max_nesting >= 4


def test_rust_flat_match_complexity():
    """A flat match (all arms are simple expressions) should count as 1 CCN
    point for the match itself; individual arms should NOT add CCN."""
    results = _walk("rust/flat_match.rs", "rust")
    flat = _find(results, "flat_match")
    if flat is None:
        pytest.skip("rust function not detected")
    # flat_match: base CCN 1 + match 1 = 2
    assert flat.ccn == 2, f"flat match CCN expected 2, got {flat.ccn}"

    cplx = _find(results, "complex_match")
    if cplx is None:
        pytest.skip("rust complex_match not detected")
    # complex_match: match has an arm with nested `if`, so arms count
    # individually. CCN = 1 (base) + 3 arms + 1 (if in arm) = 5
    assert cplx.ccn > flat.ccn, (
        f"complex match CCN ({cplx.ccn}) should exceed flat match CCN ({flat.ccn})"
    )

    multi = _find(results, "multi_stmt_match")
    if multi is None:
        pytest.skip("rust multi_stmt_match not detected")
    # multi_stmt_match: arm with multi-statement block → complex match
    # CCN = 1 (base) + 3 arms = 4
    assert multi.ccn > flat.ccn, (
        f"multi-stmt match CCN ({multi.ccn}) should exceed flat match CCN ({flat.ccn})"
    )


def test_unsupported_language_returns_empty():
    results = walk_file_complexity("/tmp/x.unknown", "klingon", b"")
    assert results == []


# ---- class-level metrics (LCOM4) -----------------------------------------


def test_python_class_cohesion():
    classes = _walk_classes("python/classes.py", "python")
    cohesive = classes.get("Cohesive")
    splintered = classes.get("Splintered")
    assert cohesive is not None and splintered is not None
    # All methods collaborate around shared state → single component.
    assert cohesive.lcom4 == 1
    # Two disjoint field clusters + a loner → three components.
    assert splintered.lcom4 == 3
    assert splintered.method_count == 5
    assert splintered.field_count == 2


def test_typescript_class_cohesion():
    classes = _walk_classes("typescript/classes.ts", "typescript")
    cohesive = classes.get("Cohesive")
    splintered = classes.get("Splintered")
    if cohesive is None or splintered is None:
        pytest.skip("typescript classes not detected")
    assert cohesive.lcom4 == 1
    assert splintered.lcom4 == 3


def test_class_metrics_carry_methods_and_size():
    classes = _walk_classes("python/classes.py", "python")
    cohesive = classes["Cohesive"]
    # methods are the same FunctionComplexity objects the function pass found
    assert len(cohesive.methods) == cohesive.method_count
    assert cohesive.total_nloc > 0
    assert cohesive.max_method_ccn >= 1


def test_unsupported_language_has_no_classes():
    fcx = walk_file("/tmp/x.unknown", "klingon", b"")
    assert fcx.classes == []
    assert fcx.functions == []


# ---- assertion-block detection (test-quality) ----------------------------


def test_python_assertion_blocks():
    results = _walk("python/assertions.py", "python")
    many = _find(results, "test_many_bare_asserts")
    assert many is not None
    # One uninterrupted run of 16 bare asserts.
    assert len(many.assertion_blocks) == 1
    assert many.assertion_blocks[0][2] == 16

    calls = _find(results, "test_unittest_calls")
    assert calls is not None
    # self.assertEqual / assertTrue calls counted as assertions.
    assert calls.assertion_blocks[0][2] == 3


def test_python_assertion_runs_split_on_non_assert():
    results = _walk("python/assertions.py", "python")
    split = _find(results, "test_split_runs")
    assert split is not None
    # A non-assert statement between the asserts breaks the run into two.
    assert [b[2] for b in split.assertion_blocks] == [2, 2]


def test_python_single_assert_is_not_a_block():
    results = _walk("python/assertions.py", "python")
    few = _find(results, "test_few_asserts")
    assert few is not None
    # Two asserts separated by an assignment → no run of ≥2.
    assert few.assertion_blocks == []


def test_typescript_expect_blocks():
    results = _walk("typescript/assertions.ts", "typescript")
    many = _find(results, "testManyExpects")
    if many is None:
        pytest.skip("typescript function not detected")
    assert many.assertion_blocks[0][2] == 16
    few = _find(results, "testFewExpects")
    assert few is not None
    assert few.assertion_blocks == []
