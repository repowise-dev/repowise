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

    def test_finds_defs_without_a_parameter_list(self, parser: ASTParser) -> None:
        source = b"""\
class Repo {
  def name: String = "r"
  def size = 3
  def run(): Unit = ()
  def build(a: Int)(b: Int): Int = a + b
  def all: List[Int]
  @deprecated def old: Int = 1
}
"""
        fi = _make_file_info("scala_pkg/Repo.scala", "scala")
        result = parser.parse_file(fi, source)
        methods = [(s.name, s.parent_name) for s in result.symbols if s.kind == "method"]
        assert sorted(methods) == sorted(
            (name, "Repo") for name in ("name", "size", "run", "build", "all", "old")
        )

    def test_finds_scala3_extension_def_without_a_parameter_list(self, parser: ASTParser) -> None:
        source = b"""\
object Ext {
  extension (s: String) def shout: String = s.toUpperCase
}
"""
        fi = _make_file_info("scala_pkg/Ext.scala", "scala")
        result = parser.parse_file(fi, source)
        assert [(s.name, s.parent_name) for s in result.symbols if s.kind == "method"] == [
            ("shout", "Ext")
        ]

    def test_annotated_abstract_members_keep_their_annotations(self, parser: ASTParser) -> None:
        source = b"""\
trait T {
  @deprecated def all: List[Int]
  @deprecated def get(i: Int): Int
  def plain: Int
}
"""
        fi = _make_file_info("scala_pkg/T.scala", "scala")
        result = parser.parse_file(fi, source)
        methods = {s.name: s.decorators for s in result.symbols if s.kind == "method"}
        assert methods == {"all": ["@deprecated"], "get": ["@deprecated"], "plain": []}
