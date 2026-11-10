"""A case arm's condition is its label, not the statements of its body."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

_EXT = {"swift": "swift", "kotlin": "kt", "rust": "rs", "java": "java"}


def _conditions(language: str, source: str) -> list[tuple[str, int]]:
    fcx = walk_file(f"/tmp/sample.{_EXT[language]}", language, source.encode())
    if not fcx.functions:
        pytest.skip(f"{language} grammar unavailable")
    return sorted(
        (c.enclosing_construct, c.operator_count) for fn in fcx.functions for c in fn.complex_conditions
    )


def test_swift_case_body_is_not_its_condition() -> None:
    # swift-nio HTTPEncoder.swift shape: a plain ``case .head(var r):`` arm
    # whose body computes booleans.
    source = """
func write(_ data: Part, a: Bool, b: Bool, c: Bool) {
    switch data {
    case .head(var response):
        let x = a && b || c
        use(response, x)
    case .end:
        break
    }
}
"""
    assert _conditions("swift", source) == []


def test_a_case_label_condition_still_counts() -> None:
    kotlin = """fun f(x: Int, a: Boolean, b: Boolean, c: Boolean): Int = when {
    a && b || c -> 1
    else -> 0
}"""
    assert _conditions("kotlin", kotlin) == [("case", 2)]
