"""Overloads and same-named generic siblings get their own symbol ids.

Java, C# and C++ let one scope declare a name more than once. The ids used to
collide, so the graph kept one node per overload set (whichever declaration was
written last) and every call to any overload drew an edge to that one line. A
colliding member now carries a discriminator, ``#<parameter count>``, and a C#
generic type beside a same-named one carries its arity, ``IFoo`1``. Calls narrow
to the member their argument count names.

The controls: a name declared once keeps its plain id, two overloads of one
arity stay one node, and a language without a registered discriminator is
untouched.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.analysis.actions.rules._text import code
from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.graph.builder import GraphBuilder
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.return_types import signature_parameter_range
from repowise.core.ingestion.symbol_identity import (
    base_symbol_id,
    id_segment_name,
    overload_sets,
    split_symbol_id,
)
from repowise.core.ingestion.traverser import FileTraverser
from repowise.core.ingestion.type_names import type_argument_count
from repowise.server.mcp_server._symbol_lookup import bare_name

_PARSER = ASTParser()
_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _info(rel: str, abs_path: str, language: str, size: int) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=abs_path,
        language=language,  # type: ignore[arg-type]
        size_bytes=size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _parse(rel: str, language: str, text: str, root: Path | None = None) -> ParsedFile:
    """Parse *text*; with *root*, also write it there, where receiver typing reads it."""
    abs_path = rel
    if root is not None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        abs_path = str(path)
    return _PARSER.parse_file(_info(rel, abs_path, language, len(text)), text.encode("utf-8"))


def _ids(parsed: ParsedFile) -> list[str]:
    return [s.id for s in parsed.symbols]


def _edges(root: Path, files: dict[str, tuple[str, str]]) -> set[tuple[str, str]]:
    parsed = {rel: _parse(rel, lang, text, root) for rel, (lang, text) in files.items()}
    resolver = CallResolver(
        parsed, {rel: set(parsed) - {rel} for rel in parsed}, repo_path=str(root)
    )
    return {
        (rc.caller_id, rc.callee_id)
        for rel, pf in parsed.items()
        for rc in resolver.resolve_file(rel, pf.calls)
    }


def _graph(files: dict[str, tuple[str, str]]):
    builder = GraphBuilder()
    for rel, (lang, text) in files.items():
        builder.add_file(_parse(rel, lang, text))
    return builder.build()


VALIDATE = """package app;

public final class Validate {
    public static void notNull(Object obj) {
        notNull(obj, "must not be null");
    }

    public static void notNull(Object obj, String msg) {
        if (obj == null) throw new IllegalArgumentException(msg);
    }

    public static void fail(String msg, Object... args) {
        throw new IllegalStateException(msg);
    }

    public static void fail(String msg) {
        fail(msg, new Object[0]);
    }

    public static void once() {
    }
}
"""

CALLER = """package app;

public class Caller {
    public void run(Object o) {
        Validate.notNull(o);
        Validate.notNull(o, "o");
        Validate.fail("x");
        Validate.fail("x", 1, 2);
        Validate.once();
    }
}
"""


class TestIdGrammar:
    @pytest.mark.parametrize(
        ("symbol_id", "base", "payload"),
        [
            ("V.java::V::notNull#1", "V.java::V::notNull", "1"),
            ("lib.rs::f#cfg(not(unix))", "lib.rs::f", "cfg(not(unix))"),
            ("V.java::V::notNull", "V.java::V::notNull", None),
            # A TypeScript private member names itself with `#`; it is no payload.
            ("a.ts::Box::#secret", "a.ts::Box::#secret", None),
            ("I.cs::IFoo`1::Validate#2", "I.cs::IFoo`1::Validate", "2"),
        ],
    )
    def test_split(self, symbol_id: str, base: str, payload: str | None) -> None:
        assert split_symbol_id(symbol_id) == (base, payload)
        assert base_symbol_id(symbol_id) == base

    def test_segment_name_strips_both_discriminators(self) -> None:
        assert id_segment_name("notNull#2") == "notNull"
        assert id_segment_name("IFoo`1") == "IFoo"
        assert id_segment_name("#secret") == "#secret"
        assert bare_name("Validate::notNull#1") == "notNull"
        assert bare_name("IValidator`1") == "IValidator"

    def test_overload_sets_groups_only_suffixed_ids(self) -> None:
        assert overload_sets(["a::f#1", "a::f#2", "a::g", "a::T`1"]) == {"a::f": ["a::f#1", "a::f#2"]}

    @pytest.mark.parametrize(
        ("signature", "language", "expected"),
        [
            ("f(int a, int b) -> void", "java", (2, 2)),
            ("f() -> void", "java", (0, 0)),
            ("f(String msg, Object... args) -> void", "java", (1, None)),
            ("f(string a, params object[] rest) -> void", "csharp", (1, None)),
            ("f(int a, int b = 0, bool c = false) -> int", "csharp", (1, 3)),
            ("Ext(this Order o, bool round) -> int", "csharp", (1, 2)),
            ("f(Map<K, V> m) -> void", "java", (1, 1)),
            ("noparens", "java", None),
            ("f(int a, int b = 2) -> void", "cpp", (1, 2)),
            ("log(const char* fmt, ...) -> void", "cpp", (1, None)),
            ("emit(int level, Args&&... args) -> void", "cpp", (1, None)),
            ("v(void) -> void", "cpp", (0, 0)),
            ("operator()(int a, int b) -> bool", "cpp", (2, 2)),
            ("operator<<(std::ostream& os, const A& a) -> std::ostream", "cpp", (2, 2)),
            ("g(int (*fp)(int, int), int n) -> void", "cpp", (2, 2)),
            ("print(FMT_STRING(Char, N) fmt, T&&... args) -> void", "cpp", (1, None)),
        ],
    )
    def test_parameter_range(self, signature: str, language: str, expected) -> None:
        assert signature_parameter_range(signature, language) == expected

    @pytest.mark.parametrize(
        ("raw", "count"),
        [("IFoo<T>", 1), ("Dictionary<string, List<int>>", 2), ("IFoo<T>?", 1),
         ("Outer<T>.Inner", 0), ("Plain", 0), ("Foo<T>[]", 1)],
    )
    def test_type_argument_count(self, raw: str, count: int) -> None:
        assert type_argument_count(raw) == count

    def test_markdown_code_span_survives_a_backtick(self) -> None:
        assert code("a.py::f") == "`a.py::f`"
        assert code("I.cs::IFoo`1") == "`` I.cs::IFoo`1 ``"


class TestJavaOverloads:
    def test_colliding_members_get_their_parameter_count(self) -> None:
        ids = _ids(_parse("app/Validate.java", "java", VALIDATE))
        assert "app/Validate.java::Validate::notNull#1" in ids
        assert "app/Validate.java::Validate::notNull#2" in ids
        assert "app/Validate.java::Validate::fail#2" in ids
        assert "app/Validate.java::Validate::fail#1" in ids
        # Declared once: the plain id, byte for byte.
        assert "app/Validate.java::Validate::once" in ids
        assert "app/Validate.java::Validate" in ids

    def test_a_modified_method_signature_names_its_parameters(self) -> None:
        parsed = _parse("app/Validate.java", "java", VALIDATE)
        once = next(s for s in parsed.symbols if s.name == "once")
        assert once.signature == "once() -> void"

    def test_two_overloads_of_one_arity_stay_one_node_and_the_first_keeps_it(self) -> None:
        text = (
            "public class P {\n"
            "  public void put(String k) { }\n"
            "  public void put(int k) { }\n"
            "  public void put(String k, int v) { }\n"
            "}\n"
        )
        graph = _graph({"P.java": ("java", text)})
        node = graph.nodes["P.java::P::put#1"]
        assert (node["start_line"], node["signature"]) == (2, "put(String k) -> void")
        assert "P.java::P::put#2" in graph

    def test_a_record_keeps_its_canonical_constructor_beside_an_overload(self) -> None:
        text = (
            "public record Point(int x, int y) {\n"
            "  public Point(int x) { this(x, 0); }\n"
            "}\n"
        )
        ids = _ids(_parse("Point.java", "java", text))
        assert "Point.java::Point::Point#1" in ids
        assert "Point.java::Point::Point#2" in ids

    def test_calls_narrow_to_the_member_their_arguments_name(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, {"app/Validate.java": ("java", VALIDATE), "app/Caller.java": ("java", CALLER)})
        run = "app/Caller.java::Caller::run"
        targets = {callee for caller, callee in edges if caller == run}
        assert targets == {
            "app/Validate.java::Validate::notNull#1",
            "app/Validate.java::Validate::notNull#2",
            "app/Validate.java::Validate::fail#1",
            "app/Validate.java::Validate::fail#2",
            "app/Validate.java::Validate::once",
        }

    def test_an_overload_calls_its_sibling_and_never_itself(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, {"app/Validate.java": ("java", VALIDATE)})
        base = "app/Validate.java::Validate::"
        # notNull(obj) is the set's first member; its call to the two-argument
        # form used to be refused as recursion on the shared id.
        assert (base + "notNull#1", base + "notNull#2") in edges
        assert (base + "fail#1", base + "fail#2") in edges
        assert not [e for e in edges if e[0] == e[1]]

    def test_an_unknown_argument_count_falls_back_to_the_first_member(self, tmp_path: Path) -> None:
        text = (
            "import java.util.function.Consumer;\n"
            "public class R {\n"
            "  public void go() { Consumer<Object> c = Validate::notNull; c.accept(1); Validate.notNull(); }\n"
            "}\n"
        )
        edges = _edges(tmp_path, {"app/Validate.java": ("java", VALIDATE), "app/R.java": ("java", text)})
        hits = {callee for caller, callee in edges if caller == "app/R.java::R::go"}
        # A zero-argument call matches no member; it keeps the representative.
        assert hits <= {"app/Validate.java::Validate::notNull#1"}


IVALIDATOR = """namespace App;

public interface IValidator<in T> : IValidator {
    ValidationResult Validate(T instance);
}

public interface IValidator {
    ValidationResult Validate(IValidationContext context);
    bool CanValidate(Type type);
}
"""

USES = """namespace App;

public class Runner {
    private readonly IValidator _plain;

    public void Typed(IValidator<string> typed, IValidationContext ctx) {
        typed.Validate("x");
        _plain.Validate(ctx);
    }
}
"""


class TestCSharpGenericSiblings:
    def test_the_generic_sibling_carries_its_arity_and_its_members(self) -> None:
        ids = _ids(_parse("src/IValidator.cs", "csharp", IVALIDATOR))
        assert "src/IValidator.cs::IValidator`1" in ids
        assert "src/IValidator.cs::IValidator`1::Validate" in ids
        assert "src/IValidator.cs::IValidator" in ids
        assert "src/IValidator.cs::IValidator::Validate" in ids
        assert "src/IValidator.cs::IValidator::CanValidate" in ids

    def test_has_method_hangs_off_the_generic_type(self) -> None:
        graph = _graph({"src/IValidator.cs": ("csharp", IVALIDATOR)})
        assert graph.has_edge("src/IValidator.cs::IValidator`1", "src/IValidator.cs::IValidator`1::Validate")
        assert not graph.has_edge("src/IValidator.cs::IValidator", "src/IValidator.cs::IValidator`1::Validate")

    def test_a_lone_generic_type_keeps_its_plain_id(self) -> None:
        text = "namespace App;\npublic class Repo<T> {\n  public void Save(T item) { }\n}\n"
        assert _ids(_parse("Repo.cs", "csharp", text))[-2:] == ["Repo.cs::Repo", "Repo.cs::Repo::Save"]

    def test_a_receiver_reaches_the_sibling_its_type_arguments_name(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path, {"src/IValidator.cs": ("csharp", IVALIDATOR), "src/Runner.cs": ("csharp", USES)})
        targets = {callee for caller, callee in edges if caller == "src/Runner.cs::Runner::Typed"}
        assert targets == {
            "src/IValidator.cs::IValidator`1::Validate",
            "src/IValidator.cs::IValidator::Validate",
        }

    def test_csharp_overloads_narrow_by_defaults_and_params(self, tmp_path: Path) -> None:
        holder = (
            "namespace App;\npublic static class H {\n"
            "  public static int Sum(int a) => a;\n"
            "  public static int Sum(int a, int b, int c = 0) => a + b + c;\n"
            "  public static int Sum(string s, params int[] rest) => 0;\n"
            "}\n"
        )
        caller = (
            "namespace App;\npublic class C {\n"
            "  public int One() => H.Sum(1);\n"
            "  public int Two() => H.Sum(1, 2);\n"
            "  public int Four() => H.Sum(\"a\", 1, 2, 3);\n"
            "}\n"
        )
        edges = _edges(tmp_path, {"H.cs": ("csharp", holder), "C.cs": ("csharp", caller)})
        assert ("C.cs::C::One", "H.cs::H::Sum#1") in edges
        assert ("C.cs::C::Two", "H.cs::H::Sum#3") in edges
        assert ("C.cs::C::Four", "H.cs::H::Sum#2") in edges


SHAPES_H = """namespace geo {
class Shape {
 public:
  Shape();
  Shape(int w, int h = 1);
  void scale(int f);
  void scale(int fx, int fy);
  int area() const;
  int area();
  template <typename T> void put(T x);
  template <typename T> void put(T* x);
  void reset(void);
  void reset(int to);
  void once();
};
inline void Shape::scale(int f) { scale(f, f); }
inline void Shape::reset() {}
}
"""

SHAPES_CPP = """#include "shapes.h"
namespace geo {
Shape::Shape() {}
Shape::Shape(int w, int h) {}
void Shape::scale(int fx, int fy) {}
int Shape::area() const { return 0; }
int Shape::area() { return 1; }
}
"""


class TestCppOverloads:
    def test_colliding_members_get_their_parameter_count(self) -> None:
        ids = set(_ids(_parse("shapes.h", "cpp", SHAPES_H)))
        base = "shapes.h::Shape::"
        assert {base + "Shape#0", base + "Shape#2", base + "scale#1", base + "scale#2"} <= ids
        # Same arity stays one id: template overloads and const overloads.
        assert {base + "put", base + "area"} <= ids
        assert base + "once" in ids

    def test_an_in_class_declaration_and_its_inline_definition_share_an_id(self) -> None:
        """``(void)`` and ``()`` both declare no parameters."""
        parsed = _parse("shapes.h", "cpp", SHAPES_H)
        by_id: dict[str, list[bool]] = {}
        for symbol in parsed.symbols:
            by_id.setdefault(symbol.id, []).append(symbol.is_declaration)
        assert sorted(by_id["shapes.h::Shape::scale#1"]) == [False, True]
        assert sorted(by_id["shapes.h::Shape::reset#0"]) == [False, True]
        graph = _graph({"shapes.h": ("cpp", SHAPES_H)})
        assert graph.nodes["shapes.h::Shape::scale#1"]["is_declaration"] is False

    def test_a_header_declared_set_resolves_to_its_out_of_line_definitions(self, tmp_path: Path) -> None:
        header = (
            "class Pen {\n public:\n  void shift(int x);\n  void shift(int x, int y = 0, int z = 0);\n"
            "  void lift();\n};\n"
        )
        source = (
            '#include "pen.h"\n'
            "void Pen::shift(int x) {}\n"
            "void Pen::shift(int x, int y, int z) {}\n"
            "void Pen::lift() {}\n"
        )
        caller = '#include "pen.h"\nvoid draw(Pen& p) {\n  p.shift(1);\n  p.shift(1, 2);\n  p.lift();\n}\n'
        edges = _edges(
            tmp_path, {"pen.h": ("cpp", header), "pen.cpp": ("cpp", source), "draw.cpp": ("cpp", caller)}
        )
        assert {
            ("draw.cpp::draw", "pen.cpp::Pen::shift#1"),
            ("draw.cpp::draw", "pen.cpp::Pen::shift#3"),
            ("draw.cpp::draw", "pen.cpp::Pen::lift"),
        } == {edge for edge in edges if edge[0] == "draw.cpp::draw"}

    def test_each_declared_member_records_its_definition(self, tmp_path: Path) -> None:
        """Defaults written on the declaration carry to the out-of-line definition."""
        (tmp_path / "util.h").write_text("int pad(int a);\nint pad(int a, int b = 0, int c = 0);\n")
        (tmp_path / "util.cpp").write_text(
            '#include "util.h"\nint pad(int a) { return a; }\nint pad(int a, int b, int c) { return a; }\n'
        )
        (tmp_path / "main.cpp").write_text('#include "util.h"\nint main() { return pad(1, 2); }\n')
        builder = GraphBuilder(repo_path=tmp_path)
        for info in FileTraverser(tmp_path).traverse():
            builder.add_file(_PARSER.parse_file(info, Path(info.abs_path).read_bytes()))
        graph = builder.build()
        assert graph.nodes["util.h::pad#1"]["defined_by"] == "util.cpp::pad#1"
        assert graph.nodes["util.h::pad#3"]["defined_by"] == "util.cpp::pad#3"
        assert graph.has_edge("main.cpp::main", "util.cpp::pad#3")

    def test_a_set_split_between_header_and_source_keeps_its_pairing(self, tmp_path: Path) -> None:
        """One overload inline in the header, one in the .cpp: the .cpp body takes the call."""
        caller = '#include "shapes.h"\nvoid draw(geo::Shape& s) {\n  s.scale(2, 3);\n}\n'
        edges = _edges(
            tmp_path,
            {"shapes.h": ("cpp", SHAPES_H), "shapes.cpp": ("cpp", SHAPES_CPP), "draw.cpp": ("cpp", caller)},
        )
        assert ("draw.cpp::draw", "shapes.cpp::Shape::scale") in edges

    def test_defaults_and_variadics_admit_their_argument_counts(self, tmp_path: Path) -> None:
        source = (
            "int pad(int a, int b = 0) { return a; }\n"
            "int pad(int a, int b, int c) { return a; }\n"
            "void log(const char* fmt, ...) {}\n"
            "void log(int level) {}\n"
            "template <typename... Args> void emit(int level, Args&&... args) {}\n"
            "void emit() {}\n"
            "void run() {\n  pad(1);\n  pad(1, 2, 3);\n  log(1);\n  log(\"x\", 1, 2);\n"
            "  emit();\n  emit(1, 2, 3);\n}\n"
        )
        edges = _edges(tmp_path, {"u.cpp": ("cpp", source)})
        assert {
            ("u.cpp::run", "u.cpp::pad#2"),
            ("u.cpp::run", "u.cpp::pad#3"),
            ("u.cpp::run", "u.cpp::log#1"),
            ("u.cpp::run", "u.cpp::log#2"),
            ("u.cpp::run", "u.cpp::emit#0"),
            ("u.cpp::run", "u.cpp::emit#2"),
        } <= edges

    def test_a_template_function_is_a_function(self) -> None:
        """A one-line template definition used to come out as a ``class``."""
        source = (
            "template <typename T> T two(T x, T y) { return x; }\n"
            "template <typename T> T two(T x) { return x; }\n"
            "template <typename T>\nT one(T x) { return x; }\n"
        )
        parsed = _parse("t.h", "cpp", source)
        assert [(s.id, s.kind) for s in parsed.symbols] == [
            ("t.h::two#2", "function"),
            ("t.h::two#1", "function"),
            ("t.h::one", "function"),
        ]

    def test_operator_overloads(self) -> None:
        source = (
            "V V::operator-() const { return *this; }\n"
            "V V::operator-(const V& o) const { return o; }\n"
            "bool Cmp::operator()(int a) const { return a; }\n"
            "bool Cmp::operator()(int a, int b) const { return a < b; }\n"
            "bool Cmp::operator==(const Cmp& o) const { return true; }\n"
        )
        ids = set(_ids(_parse("v.cpp", "cpp", source)))
        assert {"v.cpp::V::operator-#0", "v.cpp::V::operator-#1"} <= ids
        assert {"v.cpp::Cmp::operator()#1", "v.cpp::Cmp::operator()#2"} <= ids
        assert "v.cpp::Cmp::operator==" in ids


class TestOtherLanguagesAreUntouched:
    @pytest.mark.parametrize(
        ("rel", "language", "text"),
        [
            ("m.py", "python", "def f(a):\n    pass\n\ndef f(a, b):\n    pass\n"),
            ("m.c", "c", "int f(int a) { return a; }\nint f(int a, int b) { return a; }\n"),
            ("m.kt", "kotlin", "class K {\n  fun f(a: Int) {}\n  fun f(a: Int, b: Int) {}\n}\n"),
        ],
    )
    def test_same_named_definitions_keep_one_plain_id(self, rel: str, language: str, text: str) -> None:
        ids = _ids(_parse(rel, language, text))
        assert all(base_symbol_id(i) == i for i in ids), ids


def test_fixture_ids_carry_a_discriminator_only_on_collision() -> None:
    """Every fixture symbol declared once keeps its plain id, in every language.

    A discriminator appears only on an id another symbol of the same file
    shares, and only in a language that registers one.
    """
    parser = ASTParser()
    checked = 0
    for sample in ("lang_samples", "jvm_sample", "dotnet_solution", "cpp_sample", "ts_sample", "sample_repo"):
        for info in FileTraverser(_FIXTURES / sample).traverse():
            parsed = parser.parse_file(info, Path(info.abs_path).read_bytes())
            bases = [base_symbol_id(s.id) for s in parsed.symbols]
            for symbol, base in zip(parsed.symbols, bases, strict=True):
                checked += 1
                if symbol.id == base and "`" not in symbol.id:
                    continue
                assert symbol.language in ("java", "csharp", "cpp"), symbol.id
                assert bases.count(base) > 1 or "`" in symbol.id, symbol.id
    assert checked > 500


class TestNarrowingShapes:
    """Call shapes the narrowing step must read by argument count alone."""

    def test_csharp_named_arguments_and_explicit_type_arguments(self, tmp_path: Path) -> None:
        holder = (
            "namespace App;\npublic static class H {\n"
            "  public static int Sum(int a) => a;\n"
            "  public static int Sum(int a, int b) => a + b;\n"
            "  public static T Make<T>(T v) => v;\n"
            "  public static T Make<T>(T v, int n) => v;\n"
            "}\n"
        )
        caller = (
            "namespace App;\npublic class C {\n"
            "  public int Named() => H.Sum(b: 2, a: 1);\n"
            "  public int Typed() => H.Make<int>(1, 2);\n"
            "}\n"
        )
        edges = _edges(tmp_path, {"H.cs": ("csharp", holder), "C.cs": ("csharp", caller)})
        assert ("C.cs::C::Named", "H.cs::H::Sum#2") in edges
        assert ("C.cs::C::Typed", "H.cs::H::Make#2") in edges

    def test_java_explicit_type_arguments(self, tmp_path: Path) -> None:
        util = (
            "package app;\npublic class U {\n"
            "  public static <T> T make(T v) { return v; }\n"
            "  public static <T> T make(T v, int n) { return v; }\n"
            "}\n"
        )
        caller = 'package app;\npublic class K {\n  public void go() { U.<String>make("x", 2); }\n}\n'
        edges = _edges(tmp_path, {"app/U.java": ("java", util), "app/K.java": ("java", caller)})
        assert ("app/K.java::K::go", "app/U.java::U::make#2") in edges

    def test_an_interface_default_method_calls_into_its_overload_set(self, tmp_path: Path) -> None:
        shape = (
            "package app;\npublic interface Shape {\n"
            "  default double area() { return scale(1.0); }\n"
            "  default double scale(double f) { return scale(f, 1.0); }\n"
            "  double scale(double f, double g);\n"
            "}\n"
        )
        edges = _edges(tmp_path, {"app/Shape.java": ("java", shape)})
        base = "app/Shape.java::Shape::"
        assert (base + "area", base + "scale#1") in edges
        assert (base + "scale#1", base + "scale#2") in edges

    def test_overloads_split_across_a_superclass_keep_plain_ids(self, tmp_path: Path) -> None:
        """Ids collide per class, so a base and a subclass overload stay plain."""
        base = "package app;\npublic class Base {\n  public void f(int a) { }\n}\n"
        derived = (
            "package app;\npublic class Derived extends Base {\n"
            "  public void f(int a, int b) { f(a); }\n"
            "}\n"
        )
        edges = _edges(tmp_path, {"app/Base.java": ("java", base), "app/Derived.java": ("java", derived)})
        assert edges == {("app/Derived.java::Derived::f", "app/Base.java::Base::f")}
