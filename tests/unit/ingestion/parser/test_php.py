"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

PHP_SOURCE = b"""\
<?php
namespace Sample;

use Sample\\Models\\Operation;
use Sample\\Models\\CalculationRecord;

/** Calculator for basic arithmetic. */
class Calculator extends BaseCalc implements Computable
{
    public function add(float $x, float $y): float
    {
        return $x + $y;
    }

    private function helper(): void {}
}

interface Computable
{
    public function compute(): float;
}

enum Operation
{
    case Add;
    case Subtract;
}
"""


class TestPhpParser:
    def test_finds_class(self, parser: ASTParser) -> None:
        fi = _make_file_info("php_pkg/Calculator.php", "php")
        result = parser.parse_file(fi, PHP_SOURCE)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any(s.name == "Calculator" for s in classes)

    def test_finds_interface(self, parser: ASTParser) -> None:
        fi = _make_file_info("php_pkg/Calculator.php", "php")
        result = parser.parse_file(fi, PHP_SOURCE)
        interfaces = [s for s in result.symbols if s.kind == "interface"]
        assert any(s.name == "Computable" for s in interfaces)

    def test_finds_enum(self, parser: ASTParser) -> None:
        fi = _make_file_info("php_pkg/Calculator.php", "php")
        result = parser.parse_file(fi, PHP_SOURCE)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert any(s.name == "Operation" for s in enums)

    def test_finds_methods(self, parser: ASTParser) -> None:
        fi = _make_file_info("php_pkg/Calculator.php", "php")
        result = parser.parse_file(fi, PHP_SOURCE)
        methods = [s for s in result.symbols if s.kind == "method"]
        assert any(s.name == "add" for s in methods)

    def test_parses_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("php_pkg/Calculator.php", "php")
        result = parser.parse_file(fi, PHP_SOURCE)
        assert len(result.imports) >= 1

    def test_private_visibility(self, parser: ASTParser) -> None:
        fi = _make_file_info("php_pkg/Calculator.php", "php")
        result = parser.parse_file(fi, PHP_SOURCE)
        helper = next((s for s in result.symbols if s.name == "helper"), None)
        assert helper is not None
        assert helper.visibility == "private"

    def test_no_parse_errors(self, parser: ASTParser) -> None:
        fi = _make_file_info("php_pkg/Calculator.php", "php")
        result = parser.parse_file(fi, PHP_SOURCE)
        assert result.parse_errors == []
