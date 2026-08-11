"""Unit tests for the Pascal (Delphi / Free Pascal) language pipeline.

Tests parse inline byte strings so no filesystem I/O is needed. Covers
symbols, the interface/implementation dedup, imports (including the
multi-unit ``uses`` clause), and heritage -- see docs/architecture/
language-support.md's "one .scm file + one LanguageConfig" recipe.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

# tree-sitter-pascal is a real dep in pyproject.toml, but it is sometimes
# absent from a partially-synced developer venv. Skip explicitly so the
# failure mode is "go run uv sync" rather than confusing AssertionErrors.
pytest.importorskip("tree_sitter_pascal", reason="run `uv sync --all-packages`")


def _pas(path: str = "Calc.pas") -> object:
    return _make_file_info(path, "pascal")


UNIT_SOURCE = b"""\
unit Calc;

interface

uses
  SysUtils, Classes, Ns.UnitC;

type
  TCalculator = class(TObject, IFoo)
  public
    function Add(X, Y: Integer): Integer;
  end;

  ICalcTarget = interface(IInterface)
    procedure Reset;
  end;

implementation

function TCalculator.Add(X, Y: Integer): Integer;
begin
  Result := X + Y;
end;

end.
"""


class TestPascalSymbols:
    def test_finds_class_and_top_level_kinds(self, parser: ASTParser) -> None:
        result = parser.parse_file(_pas(), UNIT_SOURCE)
        kinds = {(s.name, s.kind) for s in result.symbols}
        assert ("TCalculator", "class") in kinds
        assert ("ICalcTarget", "class") in kinds

    def test_interface_and_implementation_method_collapse_to_one_symbol(
        self, parser: ASTParser
    ) -> None:
        # TCalculator.Add is declared once in the interface section
        # (signature only) and defined once in the implementation section
        # (with a body) -- two distinct physical AST nodes for one logical
        # method. Review feedback on PR #1353: this must not surface as two
        # graph nodes.
        result = parser.parse_file(_pas(), UNIT_SOURCE)
        adds = [s for s in result.symbols if s.name == "Add"]
        assert len(adds) == 1
        sym = adds[0]
        assert sym.kind == "method"
        assert sym.parent_name == "TCalculator"

    def test_surviving_symbol_is_the_implementation_with_a_body(
        self, parser: ASTParser
    ) -> None:
        # get_symbol on a method should return the real body, not just the
        # bare interface-section prototype -- the kept symbol's line range
        # must span the implementation (with `begin ... end;`), not the
        # single-line interface declaration.
        result = parser.parse_file(_pas(), UNIT_SOURCE)
        sym = next(s for s in result.symbols if s.name == "Add")
        assert sym.end_line > sym.start_line

    def test_interface_only_method_is_kept(self, parser: ASTParser) -> None:
        # ICalcTarget.Reset has no implementation in this snippet -- an
        # interface-section-only declaration must not be dropped just
        # because *some* Pascal methods in the file have a defProc pair.
        result = parser.parse_file(_pas(), UNIT_SOURCE)
        names = {(s.name, s.parent_name) for s in result.symbols}
        assert ("Reset", "ICalcTarget") in names

    def test_overloads_are_told_apart_by_signature(self, parser: ASTParser) -> None:
        # Regression: an earlier version of the dedup keyed only on
        # (parent_name, name), so a 2-overload class with only ONE variant
        # implemented in this unit silently dropped the OTHER (unimplemented)
        # overload's interface-only declaration -- real data loss, not just
        # a duplicate. Keying on (parent_name, signature) instead tells the
        # two `Add` overloads apart because their parameter lists differ.
        src = b"""\
unit Foo;
interface
type
  TFoo = class
    function Add(X: Integer): Integer; overload;
    function Add(X: Double): Double; overload;
  end;
implementation
function TFoo.Add(X: Integer): Integer;
begin
  Result := X;
end;
end.
"""
        result = parser.parse_file(_pas(), src)
        adds = {(s.signature, s.end_line > s.start_line) for s in result.symbols if s.name == "Add"}
        assert adds == {
            ("Add(X: Integer)", True),  # implemented -- has a body, multi-line
            ("Add(X: Double)", False),  # interface-only -- no body, single-line
        }

    def test_two_classes_sharing_a_method_name_stay_distinct(self, parser: ASTParser) -> None:
        src = b"""\
unit Foo;
interface
type
  TFoo = class
    function Add(X: Integer): Integer;
  end;
  TBar = class
    function Add(X: Integer): Integer;
  end;
implementation
function TFoo.Add(X: Integer): Integer;
begin
  Result := X;
end;
function TBar.Add(X: Integer): Integer;
begin
  Result := X * 2;
end;
end.
"""
        result = parser.parse_file(_pas(), src)
        adds = {(s.name, s.parent_name) for s in result.symbols if s.name == "Add"}
        assert adds == {("Add", "TFoo"), ("Add", "TBar")}

    def test_free_function_decl_and_impl_still_dedupe(self, parser: ASTParser) -> None:
        # No class involved -- parent_name is None on both sides. Confirms
        # the dedup key handles the no-parent case, not just methods.
        src = b"""\
unit Foo;
interface
procedure DoWork(X: Integer);
implementation
procedure DoWork(X: Integer);
begin
  Writeln(X);
end;
end.
"""
        result = parser.parse_file(_pas(), src)
        matches = [s for s in result.symbols if s.name == "DoWork"]
        assert len(matches) == 1
        assert matches[0].end_line > matches[0].start_line


class TestPascalImports:
    def test_multi_unit_uses_clause_extracts_every_unit(self, parser: ASTParser) -> None:
        # Review feedback on PR #1353: `uses SysUtils, Classes;` was only
        # extracting the first unit.
        result = parser.parse_file(_pas(), UNIT_SOURCE)
        modules = {i.module_path for i in result.imports}
        assert modules == {"SysUtils", "Classes", "Ns.UnitC"}

    def test_single_unit_uses_clause(self, parser: ASTParser) -> None:
        src = b"""\
unit Only;
interface
uses
  SysUtils;
implementation
end.
"""
        result = parser.parse_file(_pas("Only.pas"), src)
        assert [i.module_path for i in result.imports] == ["SysUtils"]

    def test_separate_interface_and_implementation_uses_clauses(
        self, parser: ASTParser
    ) -> None:
        # `uses` is valid in both sections of a unit, and the query isn't
        # scoped to either -- two separate multi-unit clauses in one file
        # must not interfere with each other's extraction.
        src = b"""\
unit Foo;
interface
uses SysUtils, Classes;
implementation
uses Windows, Messages;
end.
"""
        result = parser.parse_file(_pas(), src)
        modules = {i.module_path for i in result.imports}
        assert modules == {"SysUtils", "Classes", "Windows", "Messages"}


class TestPascalHeritage:
    def test_extends_and_implements(self, parser: ASTParser) -> None:
        result = parser.parse_file(_pas(), UNIT_SOURCE)
        rels = {(r.child_name, r.kind, r.parent_name) for r in result.heritage}
        # TObject is a builtin_parent and is filtered from the graph.
        assert ("TCalculator", "implements", "IFoo") in rels
        # IInterface is also a builtin_parent for Pascal.
        assert not any(child == "ICalcTarget" for child, _kind, _parent in rels)


class TestPascalEncoding:
    """Non-ASCII (Cyrillic) content -- see PR #1353 review follow-up on
    verifying repowise's Pascal support against a Russian-language codebase.
    """

    def test_cyrillic_comments_and_string_literals_parse_cleanly(
        self, parser: ASTParser
    ) -> None:
        # Identifiers stay ASCII (matches this repo's actual Delphi
        # convention); comments and string literals carry Cyrillic text.
        # node_text() decodes via tree-sitter's own byte-accurate
        # Node.text, so multi-byte UTF-8 content must round-trip intact.
        src = """\
unit Foo;
interface
uses
  SysUtils;
type
  // Комментарий на русском про класс
  TCalculator = class(TObject)
  public
    function Add(X, Y: Integer): Integer;
  end;
implementation
function TCalculator.Add(X, Y: Integer): Integer;
begin
  Result := X + Y; // сложение чисел
  WriteLn('Привет, мир');
end;
end.
""".encode()
        result = parser.parse_file(_pas(), src)
        names = {s.name for s in result.symbols}
        assert "TCalculator" in names
        assert "Add" in names

    def test_cyrillic_identifiers_are_a_known_upstream_gap(self, parser: ASTParser) -> None:
        # tree-sitter-pascal's identifier token only accepts ASCII letters,
        # so a Cyrillic-named class/method does NOT round-trip -- this locks
        # in the current (broken) behavior as a documented gap rather than
        # letting it regress silently. If tree-sitter-pascal ships a
        # Unicode-aware identifier rule, this test should start failing and
        # can be flipped to assert correct extraction instead.
        src = """\
unit Foo;
interface
type
  TКалькулятор = class(TObject)
  public
    function Сложить(X, Y: Integer): Integer;
  end;
implementation
end.
""".encode()
        result = parser.parse_file(_pas(), src)
        names = {s.name for s in result.symbols}
        # The lexer stops at the first non-ASCII byte, so neither the class
        # nor the method name round-trips in full.
        assert "TКалькулятор" not in names
        assert "Сложить" not in names
