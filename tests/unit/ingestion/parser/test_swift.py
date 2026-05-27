"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

SWIFT_SOURCE = b"""\
import Foundation

/// Calculator for basic arithmetic.
class Calculator: Computable {
    func add(x: Double, y: Double) -> Double {
        return x + y
    }

    private func helper() {}
}

protocol Computable {
    func compute() -> Double
}

enum Operation {
    case add
    case subtract
}

struct Point {
    var x: Double
    var y: Double
}
"""


class TestSwiftParser:
    def test_finds_class(self, parser: ASTParser) -> None:
        fi = _make_file_info("swift_pkg/Calculator.swift", "swift")
        result = parser.parse_file(fi, SWIFT_SOURCE)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any(s.name == "Calculator" for s in classes)

    def test_finds_protocol(self, parser: ASTParser) -> None:
        fi = _make_file_info("swift_pkg/Calculator.swift", "swift")
        result = parser.parse_file(fi, SWIFT_SOURCE)
        interfaces = [s for s in result.symbols if s.kind == "interface"]
        assert any(s.name == "Computable" for s in interfaces)

    def test_finds_functions(self, parser: ASTParser) -> None:
        fi = _make_file_info("swift_pkg/Calculator.swift", "swift")
        result = parser.parse_file(fi, SWIFT_SOURCE)
        # Functions inside a class are upgraded to "method"
        fns = [s for s in result.symbols if s.kind in ("function", "method")]
        assert any(s.name == "add" for s in fns)

    def test_parses_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("swift_pkg/Calculator.swift", "swift")
        result = parser.parse_file(fi, SWIFT_SOURCE)
        assert len(result.imports) >= 1
        modules = {imp.module_path for imp in result.imports}
        assert any("Foundation" in m for m in modules)

    def test_private_visibility(self, parser: ASTParser) -> None:
        fi = _make_file_info("swift_pkg/Calculator.swift", "swift")
        result = parser.parse_file(fi, SWIFT_SOURCE)
        helper = next((s for s in result.symbols if s.name == "helper"), None)
        assert helper is not None
        assert helper.visibility == "private"

    def test_no_parse_errors(self, parser: ASTParser) -> None:
        fi = _make_file_info("swift_pkg/Calculator.swift", "swift")
        result = parser.parse_file(fi, SWIFT_SOURCE)
        assert result.parse_errors == []
