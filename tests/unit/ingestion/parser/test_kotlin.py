"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

KOTLIN_SOURCE = b"""\
package sample

import sample.models.Operation

/** Calculator for basic arithmetic. */
class Calculator {
    fun add(x: Double, y: Double): Double {
        val result = x + y
        return result
    }

    private fun helper() {}
}

enum class Operation {
    ADD, SUBTRACT
}
"""


class TestKotlinParser:
    def test_finds_class(self, parser: ASTParser) -> None:
        fi = _make_file_info("kotlin_pkg/Calculator.kt", "kotlin")
        result = parser.parse_file(fi, KOTLIN_SOURCE)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any(s.name == "Calculator" for s in classes)

    def test_finds_functions(self, parser: ASTParser) -> None:
        fi = _make_file_info("kotlin_pkg/Calculator.kt", "kotlin")
        result = parser.parse_file(fi, KOTLIN_SOURCE)
        # add is inside Calculator so it becomes a method; check both kinds
        fns = [s for s in result.symbols if s.kind in ("function", "method")]
        assert any(s.name == "add" for s in fns)

    def test_parses_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("kotlin_pkg/Calculator.kt", "kotlin")
        result = parser.parse_file(fi, KOTLIN_SOURCE)
        assert len(result.imports) >= 1
        modules = {imp.module_path for imp in result.imports}
        assert any("Operation" in m for m in modules)

    def test_no_parse_errors(self, parser: ASTParser) -> None:
        fi = _make_file_info("kotlin_pkg/Calculator.kt", "kotlin")
        result = parser.parse_file(fi, KOTLIN_SOURCE)
        assert result.parse_errors == []

    def test_private_visibility(self, parser: ASTParser) -> None:
        fi = _make_file_info("kotlin_pkg/Calculator.kt", "kotlin")
        result = parser.parse_file(fi, KOTLIN_SOURCE)
        helper = next((s for s in result.symbols if s.name == "helper"), None)
        assert helper is not None
        assert helper.visibility == "private"
