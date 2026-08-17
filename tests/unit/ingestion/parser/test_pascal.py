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

    def test_dedup_ignores_multiline_parameter_reformatting(self, parser: ASTParser) -> None:
        # Regression: scanned against a real ~150-file Delphi codebase, 168
        # method pairs shared a class+name but escaped a raw-signature-text
        # dedup because the implementation wraps a long parameter list
        # across lines differently than the compact interface declaration
        # -- extremely common real-world Delphi formatting, not a
        # contrived edge case.
        src = b"""\
unit Foo;
interface
type
  TFoo = class
    procedure MoveSelCursor(ARowDelta, AColDelta: Integer; AExtendSel: Boolean);
  end;
implementation
procedure TFoo.MoveSelCursor(ARowDelta, AColDelta: Integer;
  AExtendSel: Boolean);
begin
end;
end.
"""
        result = parser.parse_file(_pas(), src)
        matches = [s for s in result.symbols if s.name == "MoveSelCursor"]
        assert len(matches) == 1
        assert matches[0].end_line > matches[0].start_line

    def test_dedup_ignores_identifier_case(self, parser: ASTParser) -> None:
        # Pascal is case-insensitive: TFoo.Add and TFOO.ADD name the same
        # method. The dedup key must fold case on both the class name and
        # the signature, or this reappears as two symbols.
        src = b"""\
unit Foo;
interface
type
  TCalculator = class
    function Add(X, Y: Integer): Integer;
  end;
implementation
function TCALCULATOR.ADD(X, Y: Integer): Integer;
begin
  Result := X + Y;
end;
end.
"""
        result = parser.parse_file(_pas(), src)
        matches = [s for s in result.symbols if s.name.lower() == "add"]
        assert len(matches) == 1
        assert matches[0].end_line > matches[0].start_line

    def test_anon_record_array_field_orphans_one_duplicate_but_keeps_the_real_method(
        self, parser: ASTParser
    ) -> None:
        # `array[...] of record ... end` -- an anonymous record type used
        # inline as an array element type -- has no grammar rule at all
        # (unlike a *named* `TFoo = record ... end` declaration, which
        # parses fine). Found on this repo's own uDualPanelWindow.pas: the
        # class's declType closes early at the error, and every member
        # declared after the field (Run included) gets reparented to the
        # unit's interface section instead of the class.
        #
        # A regex-based sanitizer originally blanked the anonymous record
        # before parsing to keep the class body intact. Dropped on review
        # (PR #1353): an AST-driven replacement that blanks whatever ERROR
        # nodes the grammar produces here isn't safe either -- on this
        # exact input, one of the ERROR spans tree-sitter-pascal's error
        # recovery reports is the class's own legitimate closing `end;`,
        # and blanking it produces the identical broken structure. A
        # correct fix needs a nesting-aware scanner for the record's own
        # `end` (out of scope for one construct seen in one file); this
        # documents the accepted degradation instead: Run appears twice,
        # once correctly parented and once as an orphan, rather than
        # asserting a fix that doesn't hold up.
        src = b"""\
unit Foo;
interface
type
  TFoo = class
    FTotals: array[TSide] of record
      Valid: Boolean;
      Bytes: Int64;
    end;
    procedure Run;
  end;
implementation
procedure TFoo.Run;
begin
end;
end.
"""
        result = parser.parse_file(_pas(), src)
        matches = [s for s in result.symbols if s.name == "Run"]
        assert len(matches) == 2
        parented = [s for s in matches if s.parent_name == "TFoo"]
        assert len(parented) == 1
        assert parented[0].kind == "method"


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

    def test_unit_named_in_both_uses_clauses_dedupes_to_one_import(
        self, parser: ASTParser
    ) -> None:
        # Review feedback on PR #1353: a unit named in both the interface
        # and implementation `uses` clauses of the same file produced two
        # identical Import entries -- the Pascal branch in
        # `_extract_imports` returns before the `seen_raws` dedup because
        # every moduleName match legitimately carries a different unit
        # *within* one clause (see that branch's own comment), but two
        # clauses naming the same unit is a real duplicate, not that case.
        src = b"""\
unit Foo;
interface
uses SysUtils, Classes;
implementation
uses SysUtils, Windows;
end.
"""
        result = parser.parse_file(_pas(), src)
        modules = [i.module_path for i in result.imports]
        assert sorted(modules) == ["Classes", "SysUtils", "Windows"]

    def test_dpr_unit_in_path_clause(self, parser: ASTParser) -> None:
        # Delphi/FPC project files map units to source paths right in
        # `uses` -- `MyUnit in 'src\MyUnit.pas'` -- syntax the IDE
        # generates automatically and tree-sitter-pascal's grammar has no
        # rule for at all. Hitting it used to corrupt parsing of
        # EVERYTHING after the first `in` clause (verified against this
        # repo's own MTN2.dpr: 4 imports extracted instead of ~80, the
        # 4th holding several KB of raw garbage as its module_path).
        # _sanitize_pascal_project_source blanks these before parsing.
        src = b"""\
program Foo;
uses
  SysUtils,
  MyUnit in 'src\\MyUnit.pas',
  OtherUnit in 'src\\OtherUnit.pas';
begin
end.
"""
        result = parser.parse_file(_pas("Foo.dpr"), src)
        modules = [i.module_path for i in result.imports]
        assert modules == ["SysUtils", "MyUnit", "OtherUnit"]
        assert all(len(m) < 20 for m in modules)

    def test_dpr_ifdef_block_inside_uses(self, parser: ASTParser) -> None:
        # `{$IFDEF}`/`{$ENDIF}` compiler directives are legal inside a
        # `uses` clause (also present in MTN2.dpr) -- confirm they don't
        # need special handling; Pascal treats `{$...}` as a directive
        # comment the grammar already skips.
        src = b"""\
program Foo;
uses
  {$IFDEF SOMEFLAG}
  FlagUnit,
  {$ENDIF}
  SysUtils;
begin
end.
"""
        result = parser.parse_file(_pas("Foo.dpr"), src)
        modules = {i.module_path for i in result.imports}
        assert modules == {"FlagUnit", "SysUtils"}

    def test_in_clause_sanitization_does_not_touch_pas_files(
        self, parser: ASTParser
    ) -> None:
        # Scoped to .dpr/.dpk/.lpr on purpose -- this syntax is invalid in
        # a regular unit file, so the sanitizer must not run there. Uses a
        # plain .pas path (not .dpr) with the same shape to prove it.
        src = b"""\
unit Foo;
interface
uses
  SysUtils;
implementation
end.
"""
        result = parser.parse_file(_pas("Foo.pas"), src)
        assert [i.module_path for i in result.imports] == ["SysUtils"]


class TestPascalHeritage:
    def test_extends_and_implements(self, parser: ASTParser) -> None:
        result = parser.parse_file(_pas(), UNIT_SOURCE)
        rels = {(r.child_name, r.kind, r.parent_name) for r in result.heritage}
        # TObject is a builtin_parent and is filtered from the graph.
        assert ("TCalculator", "implements", "IFoo") in rels
        # IInterface is also a builtin_parent for Pascal.
        assert not any(child == "ICalcTarget" for child, _kind, _parent in rels)

    def test_class_helper_extends_both_ancestor_and_extended_type(
        self, parser: ASTParser
    ) -> None:
        # `class helper(TBaseHelper) for TFoo` carries two distinct
        # relationships: the helper's own ancestor helper (`parent`, same
        # list as a plain class) and the type it extends (`for TFoo`,
        # positional -- no field name in the grammar). Both must surface.
        src = b"""\
unit HelperUnit;

interface

type
  TBaseHelper = class helper for TObject
  end;

  TFooHelper = class helper(TBaseHelper) for TFoo
    procedure Bar;
  end;

implementation

procedure TFooHelper.Bar;
begin
end;

end.
"""
        result = parser.parse_file(_pas("HelperUnit.pas"), src)
        rels = {(r.child_name, r.kind, r.parent_name) for r in result.heritage}
        assert ("TFooHelper", "extends", "TBaseHelper") in rels
        assert ("TFooHelper", "extends", "TFoo") in rels


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


class TestPascalCalls:
    """Parenless-call coverage.

    Found while stress-testing PR #1353: a parameterless procedure/method
    call idiomatically drops the `()` in Pascal (`Free;`, `inherited;`,
    `if X then DoThing;`), so the grammar never wraps it in `exprCall` --
    the original queries (all `exprCall`-anchored) silently missed every
    one of these. Checked against real MTN2 source: roughly a third of
    all call-shaped statements are parenless. Fixed by anchoring new
    patterns on the bare `statement` wrapper directly.
    """

    def test_parenless_free_function_call(self, parser: ASTParser) -> None:
        src = b"""\
unit Foo;
interface
implementation
procedure P;
begin
  Run;
end;
end.
"""
        result = parser.parse_file(_pas(), src)
        calls = {(c.target_name, c.receiver_name) for c in result.calls}
        assert ("Run", None) in calls

    def test_parenless_method_call(self, parser: ASTParser) -> None:
        src = b"""\
unit Foo;
interface
implementation
procedure P;
var
  Obj: TFoo;
begin
  Obj.Free;
end;
end.
"""
        result = parser.parse_file(_pas(), src)
        calls = {(c.target_name, c.receiver_name) for c in result.calls}
        assert ("Free", "Obj") in calls

    def test_parenless_call_inside_control_flow_bodies(self, parser: ASTParser) -> None:
        # `if/for/while/repeat/case` one-line bodies are exactly where
        # parenless calls are most idiomatic in Pascal.
        src = b"""\
unit Foo;
interface
implementation
procedure P;
var
  I: Integer;
begin
  if I > 0 then
    DoThing;
  for I := 0 to 9 do
    Process(I);
  while I > 0 do
    Step;
  case I of
    1: DoOne;
  else
    DoDefault;
  end;
end;
end.
"""
        result = parser.parse_file(_pas(), src)
        names = {c.target_name for c in result.calls}
        assert {"DoThing", "Process", "Step", "DoOne", "DoDefault"} <= names

    def test_explicit_inherited_call(self, parser: ASTParser) -> None:
        # `inherited Create;` / `inherited Create();` -- both forms.
        # Bare `inherited;` (no explicit name) is a documented gap: the
        # target name isn't in the node's own text, it needs the
        # enclosing method's name.
        src = b"""\
unit Foo;
interface
implementation
constructor TFoo.Create;
begin
  inherited Create;
  inherited Create();
  inherited;
end;
end.
"""
        result = parser.parse_file(_pas(), src)
        names = [c.target_name for c in result.calls]
        assert names.count("Create") == 2

    def test_assignment_and_goto_are_not_call_sites(self, parser: ASTParser) -> None:
        # Regression guard for the new bare-identifier/bare-exprDot
        # patterns: `assignment` is a sibling of `statement` in the
        # grammar (not nested in it) and `goto`/`label` are their own
        # node types, so neither should ever surface as a false-positive
        # call.
        src = b"""\
unit Foo;
interface
implementation
procedure P;
label
  Skip;
var
  Result: Integer;
begin
  Result := 1;
  goto Skip;
  Skip:
  Real;
end;
end.
"""
        result = parser.parse_file(_pas(), src)
        names = {c.target_name for c in result.calls}
        assert "Result" not in names
        assert "Skip" not in names
        assert "Real" in names

    def test_raise_reraise_is_not_a_call_site(self, parser: ASTParser) -> None:
        src = b"""\
unit Foo;
interface
implementation
procedure P;
begin
  try
    DoWork;
  except
    raise;
  end;
end;
end.
"""
        result = parser.parse_file(_pas(), src)
        names = {c.target_name for c in result.calls}
        assert "DoWork" in names
        # A bare re-raise has no name to capture -- nothing should be
        # emitted for it (and nothing should crash trying).
        assert None not in names
        assert "Сложить" not in names
