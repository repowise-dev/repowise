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


RUBY_TWIN_SOURCE = b"""\
class Pool
  def drain(conn)
    conn.close(true)
    close(conn)
    close(conn.close(false))
  end

  def close(x)
  end
end
"""


class TestRubyReceiverlessTwin:
    """``(call method: arguments:)`` leaves ``receiver`` unconstrained, so it
    also matches ``conn.close(true)``. That copy folds into the member call as
    its bare-name fallback; ``close(x)`` stays."""

    def test_a_member_call_arrives_once_with_its_receiver(self, parser: ASTParser) -> None:
        fi = _make_file_info("ruby_pkg/pool.rb", "ruby")
        result = parser.parse_file(fi, RUBY_TWIN_SOURCE)
        sites = sorted(
            ((c.line, c.receiver_name) for c in result.calls if c.target_name == "close"),
            key=lambda s: (s[0], s[1] or ""),
        )
        assert sites == [(3, "conn"), (4, None), (5, None), (5, "conn")]
        assert all(c.bare_name_fallback for c in result.calls if c.receiver_name == "conn")
