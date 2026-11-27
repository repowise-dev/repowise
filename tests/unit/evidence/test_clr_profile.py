""".NET profiles: dotnet-trace speedscope exports, and C# frames joined to their methods."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from repowise.core.evidence.profile_convert import convert_profile
from repowise.core.evidence.profile_formats import detect_profile_format, profile_formats
from repowise.core.evidence.profile_formats.model import frame_language
from repowise.core.frame_resolver import (
    ProfileFrame,
    parse_clr_frame,
    place_clr_frames,
    resolve_frames,
)
from repowise.core.ingestion.parser import ASTParser
from repowise.core.workspace.repo_index import IndexedSymbol
from tests.unit.ingestion.parser._helpers import _make_file_info

# A real recording of the fixture, run from D:\src\dotnettrace after
# `dotnet build -c Release` (.NET SDK 10.0.404; Calc.csproj turns optimization
# off so the JIT inlines nothing):
#   dotnet-trace collect --format speedscope -o trace.nettrace -- ./bin/Release/net10.0/Calc.exe
# dotnet-trace 10.0.750501. Frames carry no file or line, only
# ``Module!Namespace.Type.Method(params)``: the root is wherever the index is.
FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "speedscope_dotnet"
ROOT = "D:/src/dotnettrace"
SOURCES = ("src/Cart.cs", "src/Bag.cs", "src/Program.cs")


def _symbols(sources: dict[str, bytes] | None = None) -> dict[str, list[IndexedSymbol]]:
    """Each file's symbols as the C# parser ids them (overloads carry ``#N(Types)``)."""
    if sources is None:
        sources = {rel: (FIXTURE / rel).read_bytes() for rel in SOURCES}
    out: dict[str, list[IndexedSymbol]] = {}
    for rel, src in sources.items():
        parsed = ASTParser().parse_file(_make_file_info(rel, "csharp"), src)
        out[rel] = [
            IndexedSymbol(
                symbol_id=s.id,
                name=s.name,
                qualified_name=s.qualified_name,
                kind=s.kind,
                signature="",
                file_path=rel,
                start_line=s.start_line,
                end_line=s.end_line,
                visibility="public",
            )
            for s in parsed.symbols
        ]
    return out


def _run():
    return profile_formats.get("speedscope").read((FIXTURE / "trace.speedscope.json").read_bytes())


def _joined(frames, symbols, root: str = ROOT):
    placed = place_clr_frames(frames, root=root, symbols_by_file=symbols)
    return placed, resolve_frames(placed, root=root, symbols_by_file=symbols)


def _outcomes(frames, symbols) -> dict[str, tuple[str, str | None, bool] | str]:
    """Per frame name: (file, local symbol id, folded), or the skip reason."""
    placed, res = _joined(frames, symbols)
    hits = {r.index: r for r in res.resolved}
    out: dict[str, tuple[str, str | None, bool] | str] = {}
    for i, frame in enumerate(placed):
        hit = hits.get(i)
        if hit is None:
            out[frame.function] = res.reasons[i] or ""
        else:
            local = hit.symbol.symbol_id.split("::", 1)[1] if hit.symbol else None
            out[frame.function] = (hit.path, local, hit.folded)
    return out


def _frame(function: str) -> ProfileFrame:
    return ProfileFrame("", 0, "csharp", function=function)


def test_a_dotnet_trace_speedscope_export_reads_its_frames_as_csharp() -> None:
    data = (FIXTURE / "trace.speedscope.json").read_bytes()
    assert detect_profile_format(data[:4096]) == "speedscope"
    run = _run()
    # The sampler counts waiting threads too: wall clock, no ``--value-kind`` needed.
    assert (run.tool_name, run.value_kind, run.unit) == ("Microsoft.Diagnostics.Tracing.TraceEvent", "wall", "ms")
    total = next(f for f in run.frames if f.function == "Calc!Shop.Billing.Cart.Total(int32)")
    assert (total.path, total.line, total.language) == ("", 0, "csharp")
    # The runtime's own frames are .NET too; they find no file and stay outside the checkout.
    runtime = next(f for f in run.frames if f.function.startswith("System.Private.CoreLib"))
    assert runtime.language == "csharp"


def test_every_fixture_frame_joins_to_the_method_that_ran_it() -> None:
    outcomes = _outcomes(_run().frames, _symbols())
    assert {k: v for k, v in outcomes.items() if k.startswith("Calc!")} == {
        # Top-level statements: the index has no Program type to place them on.
        "Calc!Program.<Main>$(class System.String[])": "outside_root",
        # An overload pair, told apart by the parameter types its ids spell.
        "Calc!Shop.Billing.Cart.Total(int32)": ("src/Cart.cs", "Cart::Total#1(int)", False),
        "Calc!Shop.Billing.Cart.Total(class System.String)": ("src/Cart.cs", "Cart::Total#1(string)", False),
        "Calc!Shop.Billing.Cart.Count(class System.String)": ("src/Cart.cs", "Cart::Count", False),
        "Calc!Shop.Billing.Cart.Sum(int32)": ("src/Cart.cs", "Cart::Sum", False),
        # A lambda's closure class folds into the method that wrote it.
        "Calc!Shop.Billing.Cart+<>c.<Sum>b__2_0(int32)": ("src/Cart.cs", "Cart::Sum", True),
        # A nested class, its constructor included.
        "Calc!Shop.Billing.Cart+Line..ctor(int32)": ("src/Cart.cs", "Line::Line", False),
        "Calc!Shop.Billing.Cart+Line.Price()": ("src/Cart.cs", "Line::Price", False),
        "Calc!Shop.Billing.Cart+Line.Square(int32)": ("src/Cart.cs", "Line::Square", False),
        # A generic class under either instantiation, and its local function.
        "Calc!Shop.Storage.Bag`1[System.__Canon].Fill(!0,int32)": ("src/Bag.cs", "Bag::Fill", False),
        "Calc!Shop.Storage.Bag`1[System.Int32].Fill(!0,int32)": ("src/Bag.cs", "Bag::Fill", False),
        "Calc!Shop.Storage.Bag`1[System.__Canon].<Fill>g__Weigh|1_0()": ("src/Bag.cs", "Bag::Fill", True),
        "Calc!Shop.Storage.Bag`1[System.Int32].<Fill>g__Weigh|1_0()": ("src/Bag.cs", "Bag::Fill", True),
        # The compiler's constructor, which runs only the field initializer: no symbol.
        "Calc!Shop.Storage.Bag`1[System.__Canon]..ctor()": "no_span",
    }


def test_the_fixture_converts_with_almost_all_repo_time_joined() -> None:
    run = _run()
    symbols = _symbols()
    placed = place_clr_frames(run.frames, root=ROOT, symbols_by_file=symbols)
    conv = convert_profile(replace(run, frames=placed), root=ROOT, symbols_by_file=symbols)
    assert conv.skip_rate <= 0.05
    self_ms = {s["function"]: s["self"] for s in conv.document["symbols"]}
    # Price runs 20 iterations per call, 8 million calls: the hottest method.
    assert max(self_ms, key=self_ms.__getitem__) == "src.Cart.Line.Price"
    # The local function's samples are its parent's.
    assert "src.Bag.Bag.Fill" in self_ms
    assert not any("Weigh" in name or "b__" in name for name in self_ms)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Calc!Shop.Billing.Cart.Total(int32)", ("Shop.Billing", ("Cart",), "Total", ("int",), None)),
        ("Calc!Shop.Billing.Cart+Line..ctor(int32)", ("Shop.Billing", ("Cart", "Line"), ".ctor", ("int",), None)),
        ("Calc!Shop.Storage.Bag`1[System.Int32]..cctor()", ("Shop.Storage", ("Bag",), ".cctor", (), None)),
        ("Calc!Shop.Billing.Cart+<>c.<Sum>b__2_0(int32)", ("Shop.Billing", ("Cart",), "<Sum>b__2_0", ("int",), "Sum")),
        ("Calc!Shop.Billing.Cart+<>c.<.cctor>b__5_0()", ("Shop.Billing", ("Cart",), "<.cctor>b__5_0", (), ".cctor")),
        ("Calc!App.Jobs+<RunAsync>d__3.MoveNext()", ("App", ("Jobs",), "MoveNext", (), "RunAsync")),
        ("Calc!App.Jobs+<>c__DisplayClass2_0.<Go>b__0(class System.Object)", ("App", ("Jobs",), "<Go>b__0", ("object",), "Go")),
        ("Calc!Free.Run(class System.String[],int32&,valuetype System.Nullable`1<int64>)",
         ("", ("Free",), "Run", ("string[]", "int", "long?"), None)),
        ("Calc!A.B`2[System.__Canon,System.Int32].Map(class System.Func`2<!0,!1>,unsigned int8[0...,0...])",
         ("A", ("B",), "Map", ("Func`2", "byte[]"), None)),
        ("Calc!A.B.Get(class System.Collections.Generic.List`1<class System.String>,valuetype A.Point)",
         ("A", ("B",), "Get", ("List`1", "Point"), None)),
    ],
)
def test_dotnet_frame_spellings_parse(text: str, expected: tuple) -> None:
    name = parse_clr_frame(text)
    assert name is not None
    assert (name.namespace, name.types, name.method, name.params, name.enclosing) == expected


@pytest.mark.parametrize(
    "text",
    [
        "calc.Calc.main(String[])",  # a JVM frame
        "example.com/calc.Primes",  # Go
        "Flask.__init__",  # Python
        "libc.so.6!__memmove_avx_unaligned",  # a native frame: no parameter list
        "(garbage collector)",
        "UNMANAGED_CODE_TIME",
    ],
)
def test_names_of_other_runtimes_are_not_dotnet_frames(text: str) -> None:
    assert parse_clr_frame(text) is None


def test_frame_language_reads_a_dotnet_name_without_a_file() -> None:
    assert frame_language("", "Calc!Shop.Billing.Cart.Total(int32)") == "csharp"
    assert frame_language("", "calc.Calc.main(String[])") == "java"
    # A frame with a file keeps the file's language.
    assert frame_language("D:/src/app/main.py", "Calc!A.B.C()") == "python"


def test_namespaces_place_frames_whatever_the_directories_say() -> None:
    a = b"namespace Acme.Billing;\npublic class Ledger { public void Post() { } }\n"
    b = b"namespace Acme.Shipping\n{\n    public class Ledger { public void Post() { } }\n}\n"
    nested = (
        b"namespace Acme\n{\n    namespace Deep\n    {\n"
        b"        public class Outer\n        {\n            public struct Node { public void Walk() { } }\n"
        b"        }\n    }\n}\n"
    )
    symbols = _symbols({"x/One.cs": a, "y/Two.cs": b, "z/Three.cs": nested})
    outcomes = _outcomes(
        [
            _frame("App!Acme.Billing.Ledger.Post()"),
            _frame("App!Acme.Shipping.Ledger.Post()"),
            _frame("App!Acme.Deep.Outer+Node.Walk()"),
            _frame("App!Acme.Other.Ledger.Post()"),
        ],
        symbols,
    )
    assert outcomes == {
        "App!Acme.Billing.Ledger.Post()": ("x/One.cs", "Ledger::Post", False),
        "App!Acme.Shipping.Ledger.Post()": ("y/Two.cs", "Ledger::Post", False),
        "App!Acme.Deep.Outer+Node.Walk()": ("z/Three.cs", "Node::Walk", False),
        "App!Acme.Other.Ledger.Post()": "outside_root",
    }


def test_a_partial_type_goes_to_the_file_that_declares_the_method() -> None:
    one = b"namespace P;\npublic partial class Svc { public void Load() { } }\n"
    two = b"namespace P;\npublic partial class Svc { public void Save() { } }\n"
    both = b"namespace P;\npublic partial class Svc { public void Save(int x) { } }\n"
    outcomes = _outcomes(
        [_frame("App!P.Svc.Load()"), _frame("App!P.Svc.Save()")],
        _symbols({"Svc.Load.cs": one, "Svc.Save.cs": two}),
    )
    assert outcomes == {
        "App!P.Svc.Load()": ("Svc.Load.cs", "Svc::Load", False),
        "App!P.Svc.Save()": ("Svc.Save.cs", "Svc::Save", False),
    }
    # Two files declaring the method: neither is guessed.
    torn = _outcomes([_frame("App!P.Svc.Save()")], _symbols({"a/Svc.cs": two, "b/Svc.cs": both}))
    assert torn == {"App!P.Svc.Save()": "outside_root"}


def test_a_vendored_copy_of_a_type_does_not_hide_the_maintained_one() -> None:
    src = b"namespace V;\npublic class Json { public void Read() { } }\n"
    outcomes = _outcomes(
        [_frame("App!V.Json.Read()")],
        _symbols({"src/Json.cs": src, "vendor/lib/Json.cs": src}),
    )
    assert outcomes == {"App!V.Json.Read()": ("src/Json.cs", "Json::Read", False)}


OVERLOADS = b"""namespace O;
public class Calc
{
    public int Add(int a, int b) => a + b;
    public long Add(long a, long b) => a + b;
    public void Put(String s, int n) { }
    public void Put(Int32 x, int n) { }
    public void Map(System.Func<int, int> f) { }
    public void Map(System.Func<int, int, int> f) { }
    public void Run(int a) { System.Action x = () => { }; }
    public void Run(string a) { }
    public static System.Func<int> Seed = () => 1;
}
"""


def test_overloads_join_by_the_types_a_frame_writes_and_never_by_name_alone() -> None:
    outcomes = _outcomes(
        [
            _frame("App!O.Calc.Add(int32,int32)"),
            _frame("App!O.Calc.Add(int64,int64)"),
            # The id spells the type as written (``String``, ``Int32``); the frame the CLR's way.
            _frame("App!O.Calc.Put(class System.String,int32)"),
            _frame("App!O.Calc.Put(int32,int32)"),
            # Overloads apart only by generic arity carry it in their ids.
            _frame("App!O.Calc.Map(class System.Func`2<int32,int32>)"),
            _frame("App!O.Calc.Map(class System.Func`3<int32,int32,int32>)"),
            # Types no overload spells: the name alone picks none.
            _frame("App!O.Calc.Add(float64,float64)"),
            # A lambda in one of two overloads: nothing tells which.
            _frame("App!O.Calc+<>c.<Run>b__4_0()"),
            # A lambda in a static field initializer runs at type load: the file's.
            _frame("App!O.Calc+<>c.<.cctor>b__9_0()"),
        ],
        _symbols({"O/Calc.cs": OVERLOADS}),
    )
    assert outcomes == {
        "App!O.Calc.Add(int32,int32)": ("O/Calc.cs", "Calc::Add#2(int,int)", False),
        "App!O.Calc.Add(int64,int64)": ("O/Calc.cs", "Calc::Add#2(long,long)", False),
        "App!O.Calc.Put(class System.String,int32)": ("O/Calc.cs", "Calc::Put#2(String,int)", False),
        "App!O.Calc.Put(int32,int32)": ("O/Calc.cs", "Calc::Put#2(Int32,int)", False),
        "App!O.Calc.Map(class System.Func`2<int32,int32>)": ("O/Calc.cs", "Calc::Map#1(Func`2)", False),
        "App!O.Calc.Map(class System.Func`3<int32,int32,int32>)": ("O/Calc.cs", "Calc::Map#1(Func`3)", False),
        "App!O.Calc.Add(float64,float64)": "ambiguous",
        "App!O.Calc+<>c.<Run>b__4_0()": "ambiguous",
        "App!O.Calc+<>c.<.cctor>b__9_0()": ("O/Calc.cs", None, True),
    }


def test_a_frame_without_placement_or_name_is_not_joined() -> None:
    symbols = _symbols()
    # Placed by hand on the file, but not a .NET name: nothing to join on.
    frame = ProfileFrame(f"{ROOT}/src/Cart.cs", 0, "csharp", function="Total")
    assert resolve_frames([frame], root=ROOT, symbols_by_file=symbols).skipped == {"no_second_signal": 1}
    # A frame that already carries a file is left where it is.
    kept = ProfileFrame(f"{ROOT}/src/Bag.cs", 0, "csharp", function="Calc!Shop.Billing.Cart.Sum(int32)")
    assert place_clr_frames([kept], root=ROOT, symbols_by_file=symbols) == (kept,)


def test_async_and_iterator_state_machines_fold_into_their_method() -> None:
    src = (
        b"using System.Collections.Generic;\nusing System.Threading.Tasks;\nnamespace J;\n"
        b"public class Jobs\n{\n"
        b"    public async Task RunAsync() { await Task.Yield(); }\n"
        b"    public IEnumerable<int> Items() { yield return 1; }\n"
        b"}\n"
    )
    outcomes = _outcomes(
        [_frame("App!J.Jobs+<RunAsync>d__0.MoveNext()"), _frame("App!J.Jobs+<Items>d__1.MoveNext()")],
        _symbols({"J/Jobs.cs": src}),
    )
    assert outcomes == {
        "App!J.Jobs+<RunAsync>d__0.MoveNext()": ("J/Jobs.cs", "Jobs::RunAsync", True),
        "App!J.Jobs+<Items>d__1.MoveNext()": ("J/Jobs.cs", "Jobs::Items", True),
    }


def test_static_initializers_and_closure_constructors_go_to_the_file_not_a_constructor() -> None:
    src = (
        b"namespace S;\npublic class Cart\n{\n"
        b"    static readonly int Seed = 1;\n"
        b"    public Cart(int n) { System.Func<int> f = () => n; }\n"
        b"}\n"
    )
    frames = [
        # The static initializer: the runtime names it ``.cctor``; the source has no such member.
        _frame("App!S.Cart..cctor()"),
        # The closure class ``n`` is captured in: built inside the constructor's body.
        _frame("App!S.Cart+<>c__DisplayClass1_0..ctor()"),
        _frame("App!S.Cart+<>c..cctor()"),
        _frame("App!S.Cart..ctor(int32)"),
    ]
    assert _outcomes(frames, _symbols({"S/Cart.cs": src})) == {
        "App!S.Cart..cctor()": ("S/Cart.cs", None, True),
        "App!S.Cart+<>c__DisplayClass1_0..ctor()": ("S/Cart.cs", None, True),
        "App!S.Cart+<>c..cctor()": ("S/Cart.cs", None, True),
        "App!S.Cart..ctor(int32)": ("S/Cart.cs", "Cart::Cart", False),
    }


def test_a_static_initializer_of_a_partial_type_is_not_put_on_the_constructors_file() -> None:
    fields = b"namespace P;\npublic partial class Svc { static System.Func<int> F = () => 1; }\n"
    # (The index records no constructor written on its class's line.)
    ctor = b"namespace P;\npublic partial class Svc\n{\n    public Svc() { }\n}\n"
    frames = [_frame("App!P.Svc+<>c.<.cctor>b__0_0()"), _frame("App!P.Svc..cctor()"), _frame("App!P.Svc..ctor()")]
    assert _outcomes(frames, _symbols({"Svc.Fields.cs": fields, "Svc.cs": ctor})) == {
        "App!P.Svc+<>c.<.cctor>b__0_0()": "outside_root",
        "App!P.Svc..cctor()": "outside_root",
        "App!P.Svc..ctor()": ("Svc.cs", "Svc::Svc", False),
    }


def test_sibling_types_on_one_line_are_not_nested_in_each_other() -> None:
    src = (
        b"namespace N { public class A { public void Go() { } } public class B { public void Run() { } }"
        b" class C { class D { public void Up() { } } } }\n"
    )
    # A file named after a type on the line: the file's name opens every qualified name.
    path = "src/A.cs"
    frames = [_frame("App!N.A.Go()"), _frame("App!N.B.Run()"), _frame("App!N.C+D.Up()")]
    assert _outcomes(frames, _symbols({path: src})) == {
        "App!N.A.Go()": (path, "A::Go", False),
        "App!N.B.Run()": (path, "B::Run", False),
        "App!N.C+D.Up()": (path, "D::Up", False),
    }
