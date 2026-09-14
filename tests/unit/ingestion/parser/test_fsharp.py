"""Unit tests for the F# language pipeline.

Tests parse inline byte strings so no filesystem I/O is needed. Covers
symbols (including each clause of a ``let rec ... and`` group), the nested
binding filter, imports, calls, type references, docstrings and heritage --
see docs/architecture/language-support.md's "one .scm file + one
LanguageConfig" recipe.
"""

from __future__ import annotations

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info


def _fs(path: str = "src/Sample.fs") -> object:
    return _make_file_info(path, "fsharp")


MODULE_SOURCE = b"""\
module Acme.Widgets

open System
open System.IO
open type System.Math

/// <summary>Adds two numbers.</summary>
let add a b = a + b

let rec countDown n = countUp n
and countUp n = countDown n

let private secret = 42

type Person = { Name: string; Age: int }

type Shape =
    | Circle of float
    | Square of float

type Palette =
    | Red = 1
    | Blue = 2

type IShape =
    abstract member Area: unit -> float

type Counter(start: int) =
    let mutable count = start
    member this.Increment() = count <- count + 1
    static member Create() = Counter(0)

exception WidgetError of string

let (|Even|Odd|) n = if n % 2 = 0 then Even else Odd

let outer x =
    let inner y = y + x
    inner 3
"""


class TestFsharpSymbols:
    def test_module_header_is_a_symbol(self, parser: ASTParser) -> None:
        result = parser.parse_file(_fs(), MODULE_SOURCE)
        assert ("Acme.Widgets", "module") in {(s.name, s.kind) for s in result.symbols}

    def test_namespace_and_nested_module(self, parser: ASTParser) -> None:
        src = b"""\
namespace Acme.Domain

module Helpers =
    let ping () = 1
"""
        result = parser.parse_file(_fs(), src)
        kinds = {(s.name, s.kind) for s in result.symbols}
        assert ("Acme.Domain", "module") in kinds
        assert ("Helpers", "module") in kinds

    def test_let_bindings_split_function_from_value(self, parser: ASTParser) -> None:
        result = parser.parse_file(_fs(), MODULE_SOURCE)
        kinds = {(s.name, s.kind) for s in result.symbols}
        assert ("add", "function") in kinds
        assert ("secret", "variable") in kinds

    def test_each_and_clause_is_its_own_symbol(self, parser: ASTParser) -> None:
        # `let rec f ... and g ...` is ONE grammar node holding both clauses.
        result = parser.parse_file(_fs(), MODULE_SOURCE)
        by_name = {s.name: s for s in result.symbols}
        assert "countDown" in by_name
        assert "countUp" in by_name
        assert by_name["countDown"].start_line != by_name["countUp"].start_line

    def test_binding_span_covers_its_body(self, parser: ASTParser) -> None:
        # Without this the call sites in the body belong to whatever encloses
        # the binding, because the captured node stops at the parameter list.
        result = parser.parse_file(_fs(), MODULE_SOURCE)
        outer = next(s for s in result.symbols if s.name == "outer")
        assert outer.end_line > outer.start_line

    def test_return_type_annotation_does_not_truncate_the_span(
        self, parser: ASTParser
    ) -> None:
        src = b"""\
module Acme.Widgets

let total (xs: int list) : int =
    let seed = 0
    List.sum xs
"""
        result = parser.parse_file(_fs(), src)
        total = next(s for s in result.symbols if s.name == "total")
        assert total.start_line == 3
        assert total.end_line == 5

    def test_members_are_methods_of_their_type(self, parser: ASTParser) -> None:
        result = parser.parse_file(_fs(), MODULE_SOURCE)
        members = {(s.name, s.kind, s.parent_name) for s in result.symbols}
        assert ("Increment", "method", "Counter") in members
        assert ("Create", "method", "Counter") in members
        assert ("Area", "method", "IShape") in members

    def test_class_let_binding_is_a_field_of_the_type(self, parser: ASTParser) -> None:
        result = parser.parse_file(_fs(), MODULE_SOURCE)
        count = next(s for s in result.symbols if s.name == "count")
        assert count.parent_name == "Counter"
        assert count.kind == "variable"

    def test_active_pattern_names_every_case(self, parser: ASTParser) -> None:
        result = parser.parse_file(_fs(), MODULE_SOURCE)
        names = {s.name for s in result.symbols}
        assert {"Even", "Odd"} <= names

    def test_nested_let_is_not_a_top_level_symbol(self, parser: ASTParser) -> None:
        result = parser.parse_file(_fs(), MODULE_SOURCE)
        assert "inner" not in {s.name for s in result.symbols}

    def test_same_name_in_two_nested_modules_keeps_distinct_ids(
        self, parser: ASTParser
    ) -> None:
        src = b"""\
namespace Acme.Domain

module Reader =
    let run () = 1

module Writer =
    let run () = 2
"""
        result = parser.parse_file(_fs(), src)
        ids = {s.id for s in result.symbols if s.name == "run"}
        assert len(ids) == 2

    def test_private_binding_is_private(self, parser: ASTParser) -> None:
        result = parser.parse_file(_fs(), MODULE_SOURCE)
        secret = next(s for s in result.symbols if s.name == "secret")
        assert secret.visibility == "private"

    def test_internal_binding_is_internal(self, parser: ASTParser) -> None:
        src = b"module Acme.Widgets\n\nlet internal cache = 1\n"
        result = parser.parse_file(_fs(), src)
        cache = next(s for s in result.symbols if s.name == "cache")
        assert cache.visibility == "internal"

    def test_script_file_parses(self, parser: ASTParser) -> None:
        src = b"""\
#r "nuget: Acme.Widgets"
open Acme.Widgets

let greet name = printfn "%s" name
greet "world"
"""
        result = parser.parse_file(_make_file_info("build.fsx", "fsharp"), src)
        assert "greet" in {s.name for s in result.symbols}


    def test_parent_is_qualified_through_enclosing_modules(
        self, parser: ASTParser
    ) -> None:
        # Two modules in one file may each declare the same type with the
        # same member; a nearest-owner parent would give both members one id.
        src = b"""namespace Acme.Domain

module Reader =
    type Dup() =
        member this.Go() = 1

module Writer =
    type Dup() =
        member this.Go() = 2
"""
        result = parser.parse_file(_fs(), src)
        parents = {s.parent_name for s in result.symbols if s.name == "Go"}
        assert parents == {"Reader.Dup", "Writer.Dup"}
        assert len({s.id for s in result.symbols if s.name == "Go"}) == 2

    def test_type_extension_does_not_mint_a_second_type_symbol(
        self, parser: ASTParser
    ) -> None:
        # `type Circle with ...` augments a type declared elsewhere; capturing
        # it as a symbol put a second `Circle` under the id of the first.
        src = b"""module Acme.Widgets

type Circle() =
    member _.Area = 1.0

type Circle with
    member _.Perimeter = 2.0
"""
        result = parser.parse_file(_fs(), src)
        assert len([s for s in result.symbols if s.name == "Circle"]) == 1
        perimeter = next(s for s in result.symbols if s.name == "Perimeter")
        assert perimeter.parent_name == "Circle"

    def test_let_inside_a_do_block_is_not_a_module_symbol(
        self, parser: ASTParser
    ) -> None:
        src = b"""module Acme.Widgets

do
    let scratch = 1
    ignore scratch
"""
        result = parser.parse_file(_fs(), src)
        assert "scratch" not in {s.name for s in result.symbols}


class TestFsharpTypeKinds:
    def test_type_kinds(self, parser: ASTParser) -> None:
        result = parser.parse_file(_fs(), MODULE_SOURCE)
        kinds = {(s.name, s.kind) for s in result.symbols}
        assert ("Person", "struct") in kinds
        assert ("Shape", "enum") in kinds
        assert ("Palette", "enum") in kinds
        assert ("Counter", "class") in kinds
        assert ("WidgetError", "class") in kinds

    def test_all_abstract_type_is_an_interface(self, parser: ASTParser) -> None:
        result = parser.parse_file(_fs(), MODULE_SOURCE)
        assert ("IShape", "interface") in {(s.name, s.kind) for s in result.symbols}

    def test_a_class_with_a_constructor_stays_a_class(self, parser: ASTParser) -> None:
        src = b"""\
module Acme.Widgets

type Handle(id: int) =
    abstract member Close: unit -> unit
    default _.Close() = ()
"""
        result = parser.parse_file(_fs(), src)
        assert ("Handle", "class") in {(s.name, s.kind) for s in result.symbols}


    def test_type_access_modifiers_are_read(self, parser: ASTParser) -> None:
        src = b"""module Acme.Widgets

type private Hidden = { A: int }

type internal Shared() =
    member _.X = 1

type Open = { B: int }
"""
        result = parser.parse_file(_fs(), src)
        visibility = {s.name: s.visibility for s in result.symbols}
        assert visibility["Hidden"] == "private"
        assert visibility["Shared"] == "internal"
        assert visibility["Open"] == "public"


class TestFsharpEncoding:
    def test_non_ascii_source_round_trips(self, parser: ASTParser) -> None:
        src = "module Acme.Widgets\n\n/// Grusse aus Munchen\nlet gruss () = \"Munchen\"\n".replace(
            "u", "ü"
        ).encode("utf-8")
        result = parser.parse_file(_fs(), src)
        names = {s.name for s in result.symbols}
        assert any(name.startswith("gr") for name in names)


class TestFsharpSignatureFiles:
    def test_fsi_keeps_imports_and_claims_no_symbols(self, parser: ASTParser) -> None:
        # The implementation grammar mis-parses a signature file, so .fsi
        # takes the regex import tier instead of an invented tree.
        src = b"""\
namespace Acme.Domain

open System

module Helpers =
    val add: int -> int -> int
"""
        result = parser.parse_file(_make_file_info("src/Helpers.fsi", "fsharp"), src)
        assert result.symbols == []
        assert [i.module_path for i in result.imports] == ["System"]


class TestFsharpImports:
    def test_open_binds_a_whole_module(self, parser: ASTParser) -> None:
        # The wildcard sentinel is what `open` means, and it is what the call
        # resolver reads to decide where a bare name may be looked up.
        result = parser.parse_file(_fs(), MODULE_SOURCE)
        opens = [i for i in result.imports if not i.raw_statement.startswith("open type")]
        assert [i.module_path for i in opens] == ["System", "System.IO"]
        assert all(i.imported_names == ["*"] for i in opens)

    def test_open_type_names_the_module_holding_the_type(
        self, parser: ASTParser
    ) -> None:
        result = parser.parse_file(_fs(), MODULE_SOURCE)
        typed = next(i for i in result.imports if i.raw_statement.startswith("open type"))
        assert typed.module_path == "System"
        assert typed.imported_names == ["Math"]

    def test_load_directive_is_not_an_import(self, parser: ASTParser) -> None:
        # `#load` names a file, not a module, and the regex tier never emitted
        # one either; a directive arriving in only one tier is worse than none.
        src = b'#load "Other.fsx"\nopen Acme.Widgets\n'
        result = parser.parse_file(_make_file_info("build.fsx", "fsharp"), src)
        assert [i.module_path for i in result.imports] == ["Acme.Widgets"]


CALL_SOURCE = b"""\
module Acme.Widgets

open System.IO

let add a b = a + b

let bare () = add 1 2
let qualified () = Path.Combine("a", "b")
let piped xs = xs |> List.rev
let instance (sb: System.Text.StringBuilder) = sb.Append "x"
"""


class TestFsharpCalls:
    def test_bare_application(self, parser: ASTParser) -> None:
        result = parser.parse_file(_fs(), CALL_SOURCE)
        assert ("add", None) in {(c.target_name, c.receiver_name) for c in result.calls}

    def test_dotted_path_splits_into_receiver_and_target(
        self, parser: ASTParser
    ) -> None:
        result = parser.parse_file(_fs(), CALL_SOURCE)
        assert ("Combine", "Path") in {
            (c.target_name, c.receiver_name) for c in result.calls
        }

    def test_pipe_right_hand_side_is_a_call(self, parser: ASTParser) -> None:
        result = parser.parse_file(_fs(), CALL_SOURCE)
        assert ("rev", "List") in {
            (c.target_name, c.receiver_name) for c in result.calls
        }

    def test_instance_method_keeps_its_receiver(self, parser: ASTParser) -> None:
        result = parser.parse_file(_fs(), CALL_SOURCE)
        assert ("Append", "sb") in {
            (c.target_name, c.receiver_name) for c in result.calls
        }

    def test_ordinary_infix_operand_is_not_a_call(self, parser: ASTParser) -> None:
        # Only `|>` applies its right-hand side; `a + b` must mint nothing.
        result = parser.parse_file(_fs(), CALL_SOURCE)
        targets = {c.target_name for c in result.calls}
        assert "b" not in targets

    def test_binding_heads_are_not_calls(self, parser: ASTParser) -> None:
        result = parser.parse_file(_fs(), CALL_SOURCE)
        targets = {c.target_name for c in result.calls}
        assert targets.isdisjoint({"let", "bare", "qualified", "piped", "instance"})

    def test_calls_belong_to_the_binding_they_sit_in(self, parser: ASTParser) -> None:
        result = parser.parse_file(_fs(), CALL_SOURCE)
        call = next(c for c in result.calls if c.target_name == "add")
        assert call.caller_symbol_id == "src/Sample.fs::bare"

    def test_core_names_are_filtered(self, parser: ASTParser) -> None:
        src = b"""\
module Acme.Widgets

let report x =
    printfn "%d" x
    Some x
"""
        result = parser.parse_file(_fs(), src)
        assert {c.target_name for c in result.calls}.isdisjoint({"printfn", "Some"})


    def test_uppercase_bare_callee_is_not_a_call(self, parser: ASTParser) -> None:
        # `Wrapper 5` builds a union case and `Buffer()` a constructor; both
        # are the node shape of `add 1 2`, and F# reserves an initial capital
        # for cases, types and constructors, so neither names a function.
        src = b"""module Acme.Widgets

type Boxed = Wrapper of int

let wrap n = Wrapper n
let make () = Buffer()
"""
        result = parser.parse_file(_fs(), src)
        assert {c.target_name for c in result.calls}.isdisjoint({"Wrapper", "Buffer"})

    def test_qualified_uppercase_callee_still_resolves(
        self, parser: ASTParser
    ) -> None:
        # The convention only speaks about BARE names: a receiver already
        # says where the name lives.
        src = b"module Acme.Widgets\n\nlet run () = Acme.Runner.Start()\n"
        result = parser.parse_file(_fs(), src)
        assert ("Start", "Acme.Runner") in {
            (c.target_name, c.receiver_name) for c in result.calls
        }


class TestFsharpTypeReferences:
    def test_annotation_is_a_type_reference_and_never_a_call(
        self, parser: ASTParser
    ) -> None:
        src = b"""\
module Acme.Widgets

let load (source: WidgetSource) = source

type Holder = { Widget: WidgetSource }
"""
        result = parser.parse_file(_fs(), src)
        assert "WidgetSource" in {t.type_name for t in result.type_refs}
        assert "WidgetSource" not in {c.target_name for c in result.calls}

    def test_builtin_types_are_not_references(self, parser: ASTParser) -> None:
        src = b"module Acme.Widgets\n\nlet count (n: int) (s: string) = n\n"
        result = parser.parse_file(_fs(), src)
        assert {t.type_name for t in result.type_refs}.isdisjoint({"int", "string"})

    def test_qualified_annotation_keeps_the_last_segment(
        self, parser: ASTParser
    ) -> None:
        src = b"module Acme.Widgets\n\nlet render (w: Acme.Core.Widget) = w\n"
        result = parser.parse_file(_fs(), src)
        assert "Widget" in {t.type_name for t in result.type_refs}


class TestFsharpDocstrings:
    def test_xml_doc_above_a_binding(self, parser: ASTParser) -> None:
        result = parser.parse_file(_fs(), MODULE_SOURCE)
        add = next(s for s in result.symbols if s.name == "add")
        assert add.docstring == "Adds two numbers."

    def test_each_and_clause_keeps_its_own_doc(self, parser: ASTParser) -> None:
        src = b"""\
module Acme.Widgets

/// First clause.
let rec ping x = pong x
/// Second clause.
and pong x = ping x
"""
        result = parser.parse_file(_fs(), src)
        docs = {s.name: s.docstring for s in result.symbols}
        assert docs["ping"] == "First clause."
        assert docs["pong"] == "Second clause."

    def test_file_level_doc_becomes_the_module_docstring(
        self, parser: ASTParser
    ) -> None:
        src = b"""\
/// <summary>Widget helpers.</summary>
module Acme.Widgets

let noop () = ()
"""
        result = parser.parse_file(_fs(), src)
        assert result.docstring == "Widget helpers."


class TestFsharpHeritage:
    def test_inherit_and_interface_implementation(self, parser: ASTParser) -> None:
        src = b"""\
module Acme.Widgets

type Widget() =
    member _.Ping() = 1

type Gadget() =
    inherit Widget()
    interface IRenderable with
        member _.Render() = ()
"""
        result = parser.parse_file(_fs(), src)
        relations = {(h.child_name, h.kind, h.parent_name) for h in result.heritage}
        assert ("Gadget", "extends", "Widget") in relations
        assert ("Gadget", "implements", "IRenderable") in relations

    def test_qualified_base_keeps_the_last_segment(self, parser: ASTParser) -> None:
        src = b"""\
module Acme.Widgets

type Gadget() =
    inherit Acme.Core.Widget()
"""
        result = parser.parse_file(_fs(), src)
        assert ("Gadget", "extends", "Widget") in {
            (h.child_name, h.kind, h.parent_name) for h in result.heritage
        }

    def test_framework_roots_are_filtered(self, parser: ASTParser) -> None:
        src = b"""\
module Acme.Widgets

type WidgetError() =
    inherit System.Exception()
"""
        result = parser.parse_file(_fs(), src)
        assert result.heritage == []


class TestPipelineRegistration:
    """F# reaches symbols through the pipeline's own parse entry point, not
    only through a direct ``ASTParser`` call.
    """

    def test_pipeline_worker_yields_symbols(self, tmp_path) -> None:
        from repowise.core.pipeline.phases.ingestion import _parse_one

        src = b"""\
module Acme.Widgets

open System

let add a b = a + b

type Person = { Name: string; Age: int }
"""
        target = tmp_path / "Sample.fs"
        target.write_bytes(src)
        file_info = _make_file_info("Sample.fs", "fsharp")
        file_info.abs_path = str(target)

        result = _parse_one((file_info, src))

        assert not isinstance(result, tuple), result
        names = {s.name for s in result.symbols}
        assert {"add", "Person"} <= names
        assert any(i.module_path == "System" for i in result.imports)
