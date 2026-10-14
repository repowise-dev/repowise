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

    def test_subscript_read_is_not_a_call(self, parser: ASTParser) -> None:
        # `items[0]` parses as a call_expression whose arguments open with "[".
        src = b"func use() {\n    let a = items[0]\n    let b = self.rows[1]\n    f(1)\n    g.h(2)\n}\n"
        fi = _make_file_info("swift_pkg/Use.swift", "swift")
        result = parser.parse_file(fi, src)
        assert {c.target_name for c in result.calls} == {"f", "h"}

    def test_member_call_on_keyword_named_variable(self, parser: ASTParser) -> None:
        # The grammar reads `copy.remove(at: 0)` as the `copy` operator on `.remove(...)`.
        # A chain keeps its first hop, as `x.remove(at: 0).foo(2)` does.
        src = (
            b"func use() {\n    var copy = xs\n    copy.remove(at: 0)\n    unsafe.run(1)\n"
            b"    consume.drain(2)\n    copy.insert(3).foo(4)\n}\n"
        )
        fi = _make_file_info("swift_pkg/Use.swift", "swift")
        result = parser.parse_file(fi, src)
        assert sorted((c.receiver_name, c.target_name) for c in result.calls) == [
            ("consume", "drain"),
            ("copy", "insert"),
            ("copy", "remove"),
            ("unsafe", "run"),
        ]

    def test_copy_and_consume_operators_are_not_calls(self, parser: ASTParser) -> None:
        src = b"func use() {\n    let y = copy x\n    let z = consume x\n    consume w.foo(1)\n}\n"
        fi = _make_file_info("swift_pkg/Use.swift", "swift")
        result = parser.parse_file(fi, src)
        assert [(c.receiver_name, c.target_name) for c in result.calls] == [("w", "foo")]

    def test_conditional_compilation_in_type_body(self, parser: ASTParser) -> None:
        src = (
            b"class Haptics {\n  #if !os(visionOS)\n    private let gen = Gen()\n  #endif\n"
            b"  func fire() {}\n}\n"
        )
        fi = _make_file_info("swift_pkg/Haptics.swift", "swift")
        result = parser.parse_file(fi, src)
        assert result.parse_errors == []
        assert "fire" in {s.name for s in result.symbols if s.parent_name == "Haptics"}
