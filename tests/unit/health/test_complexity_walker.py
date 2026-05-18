"""Unit tests for the tree-sitter complexity walker.

These tests are best-effort: tree-sitter language packs may not all be
installed in CI. The walker returns ``[]`` when a language pack is
missing, so each assertion guards with ``pytest.skip`` rather than fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.health.complexity import walk_file_complexity

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "lang_samples"


def _walk(rel_path: str, language: str):
    p = FIXTURES / rel_path
    if not p.exists():
        pytest.skip(f"fixture missing: {p}")
    results = walk_file_complexity(str(p), language, p.read_bytes())
    if not results:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    return results


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


def test_unsupported_language_returns_empty():
    results = walk_file_complexity("/tmp/x.unknown", "klingon", b"")
    assert results == []
