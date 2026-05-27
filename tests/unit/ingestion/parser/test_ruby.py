"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

RUBY_SOURCE = b"""\
# Calculator module
require_relative './models'

class Calculator < BaseCalculator
  def add(x, y)
    result = x + y
    record(x, y, result)
    result
  end

  def subtract(x, y)
    x - y
  end

  def self.create
    Calculator.new
  end
end

module Operations
  def multiply(x, y)
    x * y
  end
end
"""


class TestRubyParser:
    def test_finds_class(self, parser: ASTParser) -> None:
        fi = _make_file_info("ruby_pkg/calculator.rb", "ruby")
        result = parser.parse_file(fi, RUBY_SOURCE)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any(s.name == "Calculator" for s in classes)

    def test_finds_module(self, parser: ASTParser) -> None:
        fi = _make_file_info("ruby_pkg/calculator.rb", "ruby")
        result = parser.parse_file(fi, RUBY_SOURCE)
        modules = [s for s in result.symbols if s.kind == "module"]
        assert any(s.name == "Operations" for s in modules)

    def test_finds_methods(self, parser: ASTParser) -> None:
        fi = _make_file_info("ruby_pkg/calculator.rb", "ruby")
        result = parser.parse_file(fi, RUBY_SOURCE)
        # add/subtract are inside Calculator so they become methods
        fns = [s for s in result.symbols if s.kind in ("function", "method")]
        names = {s.name for s in fns}
        assert "add" in names
        assert "subtract" in names

    def test_parses_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("ruby_pkg/calculator.rb", "ruby")
        result = parser.parse_file(fi, RUBY_SOURCE)
        assert len(result.imports) >= 1

    def test_no_parse_errors(self, parser: ASTParser) -> None:
        fi = _make_file_info("ruby_pkg/calculator.rb", "ruby")
        result = parser.parse_file(fi, RUBY_SOURCE)
        assert result.parse_errors == []
