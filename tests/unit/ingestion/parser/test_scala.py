"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

SCALA_SOURCE = b"""\
package sample

import sample.models.{Operation, CalculationRecord}

/** Calculator for basic arithmetic. */
class Calculator extends BaseCalc with Computable {
  def add(x: Double, y: Double): Double = x + y

  private def helper(): Unit = {}
}

trait Computable {
  def compute(): Double
}

object Singleton {
  val name = "calc"
}
"""


class TestScalaParser:
    def test_finds_class(self, parser: ASTParser) -> None:
        fi = _make_file_info("scala_pkg/Calculator.scala", "scala")
        result = parser.parse_file(fi, SCALA_SOURCE)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any(s.name == "Calculator" for s in classes)

    def test_finds_trait(self, parser: ASTParser) -> None:
        fi = _make_file_info("scala_pkg/Calculator.scala", "scala")
        result = parser.parse_file(fi, SCALA_SOURCE)
        traits = [s for s in result.symbols if s.kind == "trait"]
        assert any(s.name == "Computable" for s in traits)

    def test_finds_object(self, parser: ASTParser) -> None:
        fi = _make_file_info("scala_pkg/Calculator.scala", "scala")
        result = parser.parse_file(fi, SCALA_SOURCE)
        objs = [s for s in result.symbols if s.name == "Singleton"]
        assert len(objs) >= 1

    def test_finds_functions(self, parser: ASTParser) -> None:
        fi = _make_file_info("scala_pkg/Calculator.scala", "scala")
        result = parser.parse_file(fi, SCALA_SOURCE)
        # def inside a class is promoted to "method"; top-level def stays "function"
        fns = [s for s in result.symbols if s.kind in ("function", "method")]
        assert any(s.name == "add" for s in fns)

    def test_parses_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("scala_pkg/Calculator.scala", "scala")
        result = parser.parse_file(fi, SCALA_SOURCE)
        assert len(result.imports) >= 1

    def test_no_parse_errors(self, parser: ASTParser) -> None:
        fi = _make_file_info("scala_pkg/Calculator.scala", "scala")
        result = parser.parse_file(fi, SCALA_SOURCE)
        assert result.parse_errors == []
