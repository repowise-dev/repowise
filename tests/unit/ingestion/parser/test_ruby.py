"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.traverser import FileTraverser
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
    def test_rake_file_is_discovered_and_parsed(self, parser: ASTParser, tmp_path: Path) -> None:
        task = tmp_path / "lib" / "tasks" / "cleanup.rake"
        task.parent.mkdir(parents=True)
        task.write_bytes(
            b"module Helper; def self.go; 1; end; end\n"
            b"namespace :cleanup do; task :run do; Helper.go; end; end\n"
        )

        files = list(FileTraverser(tmp_path).traverse())
        assert [f.path for f in files] == ["lib/tasks/cleanup.rake"]
        assert files[0].language == "ruby"
        result = parser.parse_file(files[0], task.read_bytes())
        assert result.parse_errors == []
        assert any(s.name == "Helper" and s.kind == "module" for s in result.symbols)

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


def test_operator_and_setter_methods_are_symbols(parser: ASTParser) -> None:
    source = (
        b"class V\n"
        b"  def ==(other); true; end\n"
        b"  def <=>(o); 0; end\n"
        b"  def [](i); end\n"
        b"  def []=(i, v); end\n"
        b"  def +(o); end\n"
        b"  def -@; end\n"
        b"  def name=(v); end\n"
        b"  def valid?; end\n"
        b"  def self.[](x); end\n"
        b"  def self.config=(c); end\n"
        b"end\n"
    )
    result = parser.parse_file(_make_file_info("lib/v.rb", "ruby"), source)
    methods = [s for s in result.symbols if s.parent_name == "V"]
    names = [s.name for s in methods]
    for name in ("==", "<=>", "[]", "[]=", "+", "-@", "name=", "valid?", "config="):
        assert name in names, name
    assert names.count("[]") == 2  # the instance and the singleton method
    ids = {s.id for s in methods}
    assert {"lib/v.rb::V::==", "lib/v.rb::V::[]=", "lib/v.rb::V::name="} <= ids
