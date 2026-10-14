"""Overloads and same-named generic siblings get their own symbol ids.

Java, C# and C++ let one scope declare a name more than once. The ids used to
collide, so the graph kept one node per overload set (whichever declaration was
written last) and every call to any overload drew an edge to that one line. A
colliding member now carries a discriminator, ``#<parameter count>``, and a C#
generic type beside a same-named one carries its arity, ``IFoo`1``. Calls narrow
to the member their argument count names.

Overloads of one arity also carry their erased parameter types,
``put#1(String)``. A Java enum constant's body methods sit under the constant,
``Op::PLUS::apply``.

The controls: a name declared once keeps its plain id, members equal in count
and types (a C++ declaration and its definition) stay one node, and a language
without a registered discriminator is untouched.
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
from repowise.core.ingestion.return_types import signature_parameter_range, signature_parameters
from repowise.core.ingestion.symbol_identity import (
    base_symbol_id,
    erased_type,
    id_segment_name,
    kotlin_erased_type,
    overload_sets,
    split_symbol_id,
    symbol_id_arity,
    untyped_symbol_id,
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

    def test_overloads_of_one_arity_split_by_erased_parameter_types(self) -> None:
        text = (
            "public class P {\n"
            "  public void put(String k) { }\n"
            "  public void put(int k) { }\n"
            "  public void put(final java.util.Map<String, Integer> m, @Nullable Object... rest) { }\n"
            "  public void put(char k[], long v) { }\n"
            "  public void put(String k, int v, int w) { }\n"
            "}\n"
        )
        graph = _graph({"P.java": ("java", text)})
        node = graph.nodes["P.java::P::put#1(int)"]
        assert (node["start_line"], node["signature"]) == (3, "put(int k) -> void")
        assert {
            "P.java::P::put#1(String)",
            # Varargs read as the array they are: no ``.`` in an id payload.
            "P.java::P::put#2(Map,Object[])",
            "P.java::P::put#2(char[],long)",
            # A count no sibling shares keeps the bare count.
            "P.java::P::put#3",
        } <= set(graph)
        assert split_symbol_id("P.java::P::put#2(char[],long)") == ("P.java::P::put", "2(char[],long)")
        assert symbol_id_arity("P.java::P::put#2(char[],long)") == "2"
        assert id_segment_name("put#2(char[],long)") == "put"
        assert bare_name("P.java::P::put#2(Map,Object[])") == "put"
        assert untyped_symbol_id("P.java::P::put#2(char[],long)") == "P.java::P::put#2"

    @pytest.mark.parametrize(
        ("parameter", "erased"),
        [
            ("Object... rest", "Object[]"),
            ("final java.util.Map<String, Integer> m", "Map"),
            ("void (*cb)(int, int)", "void*fn"),
            ("int (&row)[4]", "int&fn"),
            ("...", "[]"),
            ("Args&&... args", "Args&&[]"),
            ("const std::vector<ns::T*>& v", "vector*&"),
            ("@a.b.C(x = \"/p.q\") String s", "String"),
            ("unsigned long n", "unsigned_long"),
        ],
    )
    def test_an_erased_parameter_type_never_holds_a_name_separator(
        self, parameter: str, erased: str
    ) -> None:
        got = erased_type(parameter)
        assert got == erased
        assert not any(sep in got for sep in (".", "/", "::", "(", ")", "#"))

    def test_an_inserted_overload_renames_no_sibling(self) -> None:
        body = "  void put(String k) { }\n  void put(int k) { }\n"
        before = set(_ids(_parse("P.java", "java", "class P {\n" + body + "}\n")))
        after = set(_ids(_parse("P.java", "java", "class P {\n  void put(long k) { }\n" + body + "}\n")))
        assert before < after

    def test_enum_constant_bodies_get_their_own_methods(self, tmp_path: Path) -> None:
        text = (
            "public enum Op {\n"
            "  PLUS { int apply(int a, int b) { return a + b; } },\n"
            "  MINUS { int apply(int a, int b) { return negate(b) + a; } };\n"
            "  abstract int apply(int a, int b);\n"
            "  static int negate(int x) { return -x; }\n"
            "}\n"
        )
        by_id = {s.id: s for s in _parse("Op.java", "java", text).symbols}
        assert {"Op.java::Op::PLUS::apply", "Op.java::Op::MINUS::apply", "Op.java::Op::apply"} <= set(by_id)
        assert by_id["Op.java::Op::MINUS::apply"].qualified_name == "Op.Op.MINUS.apply"
        # The enum stays the owner: its node holds every constant's method.
        assert by_id["Op.java::Op::MINUS::apply"].parent_name == "Op"
        graph = _graph({"Op.java": ("java", text)})
        assert graph.has_edge("Op.java::Op", "Op.java::Op::PLUS::apply")
        assert graph["Op.java::Op"]["Op.java::Op::PLUS::apply"]["edge_type"] == "has_method"
        edges = _edges(tmp_path, {"Op.java": ("java", text)})
        assert ("Op.java::Op::MINUS::apply", "Op.java::Op::negate") in edges

    def test_a_same_arity_call_keeps_the_representative_and_callers_keep_their_ids(
        self, tmp_path: Path
    ) -> None:
        text = (
            "public class S {\n"
            "  public S append(String s) { return emit(s); }\n"
            "  public S append(char c) { return emit(String.valueOf(c)); }\n"
            "  S emit(String s) { return this; }\n"
            "  void use(String v) { append(v); }\n"
            "}\n"
        )
        edges = _edges(tmp_path, {"S.java": ("java", text)})
        base = "S.java::S::"
        # Each overload's own call is attributed to its own node.
        assert (base + "append#1(String)", base + "emit") in edges
        assert (base + "append#1(char)", base + "emit") in edges
        # A count cannot choose between them, nor a literal argument: the set's
        # representative, the first declared, answers, as the shared node did before.
        assert {c for a, c in edges if a == base + "use"} == {base + "append#1(String)"}

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

    def test_csharp_same_arity_constructors_get_their_own_nodes(self) -> None:
        text = (
            "namespace App;\npublic class P {\n"
            "  public P(string name) { }\n"
            "  public P(TimeSpan? delay) { }\n"
            "  public P(int[] ids) { }\n"
            "  public P() { }\n"
            "}\n"
        )
        ids = set(_ids(_parse("P.cs", "csharp", text)))
        assert {"P.cs::P::P#1(string)", "P.cs::P::P#1(TimeSpan?)", "P.cs::P::P#1(int[])", "P.cs::P::P#0"} <= ids


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
        # Same arity splits by parameter type; const overloads erase alike.
        assert {base + "put#1(T)", base + "put#1(T*)", base + "area"} <= ids
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

    @pytest.mark.parametrize("header_first", [True, False])
    def test_a_member_pairs_with_a_plain_id_definition(self, tmp_path: Path, header_first: bool) -> None:
        decls = ["int f(int a);", "int f(long a);"]
        if not header_first:
            decls.reverse()
        (tmp_path / "f.h").write_text("\n".join(decls) + "\n")
        (tmp_path / "f.cpp").write_text('#include "f.h"\nint f(int x) { return x; }\n')
        graph = self._build(tmp_path)
        assert graph.nodes["f.h::f#1(int)"]["defined_by"] == "f.cpp::f"
        assert not graph.nodes["f.h::f#1(long)"].get("defined_by")

    def test_a_type_spelled_two_ways_pairs_by_count(self, tmp_path: Path) -> None:
        (tmp_path / "g.h").write_text("int g(int a);\nint g(size_t n);\n")
        (tmp_path / "g.cpp").write_text(
            '#include "g.h"\nint g(int a) { return a; }\nint g(unsigned long n) { return 0; }\n'
        )
        graph = self._build(tmp_path)
        assert graph.nodes["g.h::g#1(int)"]["defined_by"] == "g.cpp::g#1(int)"
        assert graph.nodes["g.h::g#1(size_t)"]["defined_by"] == "g.cpp::g#1(unsigned_long)"

    @staticmethod
    def _build(root: Path):
        builder = GraphBuilder(repo_path=root)
        for info in FileTraverser(root).traverse():
            builder.add_file(_PARSER.parse_file(info, Path(info.abs_path).read_bytes()))
        return builder.build()

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
        ],
    )
    def test_same_named_definitions_keep_one_plain_id(self, rel: str, language: str, text: str) -> None:
        ids = _ids(_parse(rel, language, text))
        assert all(base_symbol_id(i) == i for i in ids), ids


class TestKotlinOverloads:
    """Kotlin overloads and same-named extensions of one scope, ``Main.kt::main``."""

    _MAIN = (
        "package app\n"
        "fun main(args: Array<String>) { }\n"
        "fun Application.main() { main(JwkProvider()) }\n"
        "fun Application.main(jwkProvider: JwkProvider) { }\n"
        'fun Byte.toHexString(): String = ""\n'
        'fun Int.toHexString(): String = ""\n'
        "fun once() { }\n"
        "class Application\nclass JwkProvider\n"
    )

    def test_each_member_gets_its_own_id_and_a_receiver_leads_its_types(self) -> None:
        ids = _ids(_parse("app/Main.kt", "kotlin", self._MAIN))
        assert ids[:6] == [
            "app/Main.kt::main#1(Array)",
            "app/Main.kt::main#0",
            "app/Main.kt::main#1(Application,JwkProvider)",
            "app/Main.kt::toHexString#0(Byte)",
            "app/Main.kt::toHexString#0(Int)",
            "app/Main.kt::once",
        ]
        assert untyped_symbol_id(ids[2]) == "app/Main.kt::main#1"

    def test_a_nullable_or_generic_receiver_is_its_own_member(self) -> None:
        """``Int?.g()`` beside ``Int.g()`` is legal: the JVM sees ``Integer`` and ``int``."""
        text = "fun Int.g() { }\nfun Int?.g() { }\nfun <T> T.h() { }\nfun <T> T?.h() { }\n"
        symbols = _parse("G.kt", "kotlin", text).symbols
        assert [s.id for s in symbols] == [
            "G.kt::g#0(Int)",
            "G.kt::g#0(Int?)",
            "G.kt::h#0(T)",
            "G.kt::h#0(T?)",
        ]
        # The dispatch type stays bare, and a type parameter extends nothing in particular.
        assert [s.extension_receiver for s in symbols] == ["Int", "Int", None, None]

    def test_a_same_erasure_collision_keeps_the_first_declaration(self) -> None:
        """``@JvmName`` lets two members erase alike; they stay one id, held by the first."""
        text = 'fun f(xs: List<String>) { }\n@JvmName("fInts")\nfun f(xs: List<Int>) { }\n'
        assert _ids(_parse("F.kt", "kotlin", text)) == ["F.kt::f", "F.kt::f"]
        assert _graph({"F.kt": ("kotlin", text)}).nodes["F.kt::f"]["start_line"] == 1

    def test_an_override_spelled_through_a_typealias_dispatches_from_its_interface(self) -> None:
        text = (
            "package app\n\ntypealias Name = String\n\n"
            "interface Store {\n  fun put(x: Int)\n  fun put(x: String)\n}\n\n"
            "class Mem : Store {\n"
            "  override fun put(x: Int) { }\n  override fun put(x: Name) { }\n}\n"
        )
        graph = _graph({"app/Store.kt": ("kotlin", text)})
        dispatched = {
            (base, impl)
            for base, impl, data in graph.edges(data=True)
            if data.get("edge_type") == "dispatches_to"
        }
        assert (
            "app/Store.kt::Store::put#1(String)",
            "app/Store.kt::Mem::put#1(Name)",
        ) in dispatched

    def test_a_property_beside_a_same_named_function_keeps_one_id(self) -> None:
        text = "class K {\n  val x: Int = 1\n  fun x() = 2\n}\n"
        assert _ids(_parse("K.kt", "kotlin", text)) == ["K.kt::K", "K.kt::K::x", "K.kt::K::x"]
        # The function holds the shared node, whichever is declared first.
        for source in (text, "class K {\n  fun x() = 2\n  val x: Int = 1\n}\n"):
            node = _graph({"K.kt": ("kotlin", source)}).nodes["K.kt::K::x"]
            assert node["kind"] == "method"

    @pytest.mark.parametrize(
        ("parameter", "erased"),
        [
            ("args: Array<String>", "Array"),
            ("sink: java.nio.ByteBuffer", "ByteBuffer"),
            ("name: String? = null", "String?"),
            ("vararg more: Int", "Int[]"),
            ("@StringRes id: Int", "Int"),
            ("block: (Int, String) -> Unit", "Function2"),
            ("block: suspend Builder.() -> Unit = {}", "Function1"),
            ("f: ((Int) -> Unit, String) -> X", "Function2"),
            ("f: ((Int) -> Unit)?", "Function1"),
            ("@Json(name = key(1, (2))) m: Map<String, (Int) -> Unit>", "Map"),
            ('@param:Json("a") y: Int', "Int"),
        ],
    )
    def test_a_parameter_erases_to_its_written_type(self, parameter: str, erased: str) -> None:
        assert kotlin_erased_type(parameter) == erased

    def test_calls_bind_by_receiver_then_by_argument_count(self, tmp_path: Path) -> None:
        io = (
            "package app\n\ninterface Source\n\ninterface Sink\n\n"
            'class BufferedSource {\n  fun readUtf8(): String = ""\n}\n\n'
            "class BufferedSink\n\n"
            "fun Source.buffer(): BufferedSource = BufferedSource()\n\n"
            "fun Sink.buffer(): BufferedSink = BufferedSink()\n"
        )
        use = (
            "package app\n"
            "fun read(s: Source) {\n  val b = s.buffer()\n  b.readUtf8()\n}\n"
            "fun write(k: Sink) { k.buffer() }\n"
        )
        files = {
            "app/Io.kt": ("kotlin", io),
            "app/Use.kt": ("kotlin", use),
            "app/Main.kt": ("kotlin", self._MAIN),
        }
        edges = _edges(tmp_path, files)
        assert ("app/Use.kt::read", "app/Io.kt::buffer#0(Source)") in edges
        assert ("app/Use.kt::write", "app/Io.kt::buffer#0(Sink)") in edges
        # Each extension's own return type types the local it assigns.
        assert ("app/Use.kt::read", "app/Io.kt::BufferedSource::readUtf8") in edges
        assert ("app/Main.kt::main#0", "app/Main.kt::main#1(Application,JwkProvider)") in edges

    def test_a_trailing_lambda_is_an_argument_and_a_default_lowers_the_floor(
        self, tmp_path: Path
    ) -> None:
        text = (
            "package app\nclass R {\n"
            "  fun exec(a: Int) { }\n"
            "  fun exec(a: Int, block: () -> Unit) { }\n"
            "  fun put(a: Int, b: Int, c: Int = 0) { }\n"
            "  fun put(a: Int) { }\n"
            "  fun go() {\n    exec(1) { }\n    put(1, 2)\n  }\n}\n"
        )
        edges = _edges(tmp_path, {"app/R.kt": ("kotlin", text)})
        assert edges == {
            ("app/R.kt::R::go", "app/R.kt::R::exec#2"),
            ("app/R.kt::R::go", "app/R.kt::R::put#3"),
        }

    def test_a_function_that_cannot_take_the_count_draws_no_edge(self, tmp_path: Path) -> None:
        """``item(key) { }`` is a library call, not the repo's ``item(a, b, onClick)``.

        An ``override`` is exempt: it inherits the defaults its interface writes.
        """
        text = (
            "package app\n\n"
            "fun item(selected: Boolean, label: String, onClick: () -> Unit) { }\n\n"
            "interface Source {\n  fun topics(ids: List<String>? = null): List<String>\n}\n\n"
            "class Remote : Source {\n"
            "  override fun topics(ids: List<String>?): List<String> = emptyList()\n"
            "  fun sync() {\n    topics()\n  }\n}\n\n"
            'fun screen() {\n  item("k") { }\n}\n'
        )
        edges = _edges(tmp_path, {"app/U.kt": ("kotlin", text)})
        assert ("app/U.kt::screen", "app/U.kt::item") not in edges
        assert ("app/U.kt::Remote::sync", "app/U.kt::Remote::topics") in edges

    def test_a_trailing_comma_closes_no_parameter(self) -> None:
        """Compose style, ``modifier: Modifier = Modifier,`` before ``)``."""
        signature = (
            "function Screen(\n    onClick: () -> Unit,\n    modifier: Modifier = Modifier,\n)"
        )
        assert signature_parameter_range(signature, "kotlin") == (1, 2)


@pytest.mark.parametrize(
    ("signature", "parameters"),
    [
        ("def f(a, b,)", ["a", "b"]),
        ("fn f(a: i32, b: &str,)", ["a: i32", "b: &str"]),
        ("function f(\n  a: Int,\n  b: Int,\n)", ["a: Int", "b: Int"]),
    ],
    ids=["python", "rust", "kotlin"],
)
def test_a_trailing_comma_is_not_a_parameter(signature: str, parameters: list[str]) -> None:
    assert [p.strip() for p in signature_parameters(signature) or []] == parameters


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
                assert symbol.language in ("java", "csharp", "cpp", "kotlin"), symbol.id
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


class TestOverloadTies:
    """A tie lands on a member that admits the call.

    Call sites carry no argument types beyond a Java literal's, so same-arity
    members tie unless the literals fit one closest (``TestJavaLiteralArguments``). A tie keeps
    the representative, the first member declared, when it is tied; a tie
    among others takes the first tied member in declaration order. Before, a
    tie always answered with the representative, even when its own arity
    could not take the call.
    """

    def test_java_a_tie_among_other_members_takes_the_first_admitting_one(
        self, tmp_path: Path
    ) -> None:
        text = (
            "package app;\npublic class Out {\n"
            "  public String charset() { return \"\"; }\n"
            "  public Out charset(String name) { return this; }\n"
            "  public Out charset(java.nio.charset.Charset cs) { return this; }\n"
            "  void use(String x) { charset(x); }\n"
            "  void read() { charset(); }\n"
            "}\n"
        )
        edges = _edges(tmp_path, {"app/Out.java": ("java", text)})
        base = "app/Out.java::Out::"
        assert {c for a, c in edges if a == base + "use"} == {base + "charset#1(String)"}
        assert {c for a, c in edges if a == base + "read"} == {base + "charset#0"}

    def test_java_a_constructor_tie_keeps_an_inbound_edge(self, tmp_path: Path) -> None:
        text = (
            "package app;\npublic class A {\n"
            "  A() { }\n"
            "  A(String s) { }\n"
            "  A(int n) { }\n"
            "  static A mk(String x) { return new A(x); }\n"
            "}\n"
        )
        edges = _edges(tmp_path, {"app/A.java": ("java", text)})
        base = "app/A.java::A::"
        assert {c for a, c in edges if a == base + "mk"} == {base + "A#1(String)"}

    def test_java_a_varargs_tie_respects_each_floor(self, tmp_path: Path) -> None:
        text = (
            "package app;\npublic class L {\n"
            "  public void log(String msg) { }\n"
            "  public void log(String fmt, Object... args) { }\n"
            "  public void log(int level, String fmt, Object... args) { }\n"
            "  void one(String m) { log(m); }\n"
            "  void three(String m) { log(m, 1, 2); }\n"
            "}\n"
        )
        edges = _edges(tmp_path, {"app/L.java": ("java", text)})
        base = "app/L.java::L::"
        assert {c for a, c in edges if a == base + "one"} == {base + "log#1"}
        # Both varargs members admit three arguments; the representative does not.
        assert {c for a, c in edges if a == base + "three"} == {base + "log#2"}

    def test_java_a_varargs_representative_in_the_tie_is_kept(self, tmp_path: Path) -> None:
        text = (
            "package app;\npublic class F {\n"
            "  public void fmt(String f, Object... args) { }\n"
            "  public void fmt(int n, Object... args) { }\n"
            "  public void fmt() { }\n"
            "  void use() { fmt(\"a\", 1); }\n"
            "}\n"
        )
        edges = _edges(tmp_path, {"app/F.java": ("java", text)})
        base = "app/F.java::F::"
        assert {c for a, c in edges if a == base + "use"} == {base + "fmt#2(String,Object[])"}

    @pytest.mark.parametrize(
        ("inherited", "expected"),
        [
            ("  public int getField(int i) { return i; }\n", "app/Base.java::Base::getField#1"),
            ("", None),
        ],
        ids=["inherited-overload", "none-admits"],
    )
    def test_java_a_callee_that_cannot_take_the_count_yields_to_an_inherited_overload(
        self, tmp_path: Path, inherited: str, expected: str | None
    ) -> None:
        """``getField(i)`` beside an override of only ``getField(int, Chronology)``."""
        base = (
            "package app;\npublic abstract class Base {\n"
            f"{inherited}  protected abstract int getField(int i, Object chrono);\n}}\n"
        )
        sub = (
            "package app;\npublic class Sub extends Base {\n"
            "  protected int getField(int i, Object chrono) { return i; }\n"
            "  int use(int i) { return getField(i); }\n}\n"
        )
        files = {"app/Base.java": ("java", base), "app/Sub.java": ("java", sub)}
        parsed = {rel: _parse(rel, lang, text, tmp_path) for rel, (lang, text) in files.items()}
        resolver = CallResolver(
            parsed,
            {rel: set(parsed) - {rel} for rel in parsed},
            repo_path=str(tmp_path),
            heritage_parents={"app/Sub.java::Sub": {"app/Base.java::Base"}},
        )
        callees = {rc.callee_id for rc in resolver.resolve_file("app/Sub.java", parsed["app/Sub.java"].calls)}
        assert callees == ({expected} if expected else set())

    def test_java_a_farther_ancestor_overload_takes_what_a_nearer_one_cannot(
        self, tmp_path: Path
    ) -> None:
        """``f(1)``: ``Mid`` declares only ``f(int, int)``, ``Top`` the ``f(int)`` that takes it."""
        files = {
            "app/Top.java": "package app;\npublic class Top {\n  public void f(int i) { }\n}\n",
            "app/Mid.java": "package app;\npublic class Mid extends Top {\n"
            "  public void f(int a, int b) { }\n}\n",
            "app/Sub.java": "package app;\npublic class Sub extends Mid {\n"
            "  public void f(int a, int b) { }\n  void use() { f(1); }\n}\n",
        }
        parsed = {rel: _parse(rel, "java", text, tmp_path) for rel, text in files.items()}
        resolver = CallResolver(
            parsed,
            {rel: set(parsed) - {rel} for rel in parsed},
            repo_path=str(tmp_path),
            heritage_parents={
                "app/Sub.java::Sub": {"app/Mid.java::Mid"},
                "app/Mid.java::Mid": {"app/Top.java::Top"},
            },
        )
        calls = parsed["app/Sub.java"].calls
        assert {rc.callee_id for rc in resolver.resolve_file("app/Sub.java", calls)} == {
            "app/Top.java::Top::f"
        }

    def test_csharp_a_tie_among_other_members_takes_the_first_admitting_one(
        self, tmp_path: Path
    ) -> None:
        holder = (
            "namespace App;\npublic static class H {\n"
            "  public static int Sum() => 0;\n"
            "  public static int Sum(int a) => a;\n"
            "  public static int Sum(string s) => 0;\n"
            "}\n"
        )
        caller = (
            "namespace App;\npublic class C {\n"
            "  public int One() => H.Sum(1);\n"
            "  public int None() => H.Sum();\n"
            "}\n"
        )
        edges = _edges(tmp_path, {"H.cs": ("csharp", holder), "C.cs": ("csharp", caller)})
        assert {c for a, c in edges if a == "C.cs::C::One"} == {"H.cs::H::Sum#1(int)"}
        assert ("C.cs::C::None", "H.cs::H::Sum#0") in edges

    def test_cpp_a_tie_on_declared_defaults_takes_the_first_admitting_one(
        self, tmp_path: Path
    ) -> None:
        header = (
            "class Pen {\n public:\n  void shift();\n"
            "  void shift(int x, int y = 0);\n  void shift(double x, double y = 0);\n};\n"
        )
        source = (
            '#include "pen.h"\n'
            "void Pen::shift() {}\n"
            "void Pen::shift(int x, int y) {}\n"
            "void Pen::shift(double x, double y) {}\n"
        )
        caller = '#include "pen.h"\nvoid draw(Pen& p) {\n  p.shift(1);\n}\nvoid lift(Pen& p) {\n  p.shift();\n}\n'
        edges = _edges(
            tmp_path, {"pen.h": ("cpp", header), "pen.cpp": ("cpp", source), "draw.cpp": ("cpp", caller)}
        )
        # Both two-parameter members admit one argument through the defaults
        # their declarations write; the no-argument representative does not.
        assert {c for a, c in edges if a == "draw.cpp::draw"} == {"pen.cpp::Pen::shift#2(int,int)"}
        assert ("draw.cpp::lift", "pen.cpp::Pen::shift#0") in edges

    def test_a_member_with_an_unknown_range_keeps_the_representative(self, tmp_path: Path) -> None:
        """A member whose parameter list cannot be read may admit any count."""
        text = (
            "package app;\npublic class P {\n"
            "  void put() { }\n"
            "  void put(int a) { }\n"
            "  void put(String s, int n) { }\n"
            "  void use() { put(1); }\n"
            "}\n"
        )
        parsed = _parse("app/P.java", "java", text, tmp_path)
        for symbol in parsed.symbols:
            if symbol.id == "app/P.java::P::put#2":
                symbol.signature = "put"  # no closed parameter list: range unknown
        resolver = CallResolver({"app/P.java": parsed}, {"app/P.java": set()}, repo_path=str(tmp_path))
        edges = {(rc.caller_id, rc.callee_id) for rc in resolver.resolve_file("app/P.java", parsed.calls)}
        # Only put#1 is known to admit one argument, but put#2 may too: the
        # count does not choose, and the representative answers.
        assert edges == {("app/P.java::P::use", "app/P.java::P::put#0")}

    @pytest.mark.parametrize(
        ("second_returns", "typed"), [("Tag", True), ("Cs", False)]
    )
    def test_csharp_a_chain_through_a_tie_needs_one_return_type(
        self, tmp_path: Path, second_returns: str, typed: bool
    ) -> None:
        # Two Label() methods; untyped, the bare-name tiers answer Cs's, the
        # one declared last.
        types = (
            "namespace App;\npublic class Tag {\n  public string Label() => \"\";\n}\n"
            "public class Cs {\n  public string Label() => \"\";\n}\n"
        )
        holder = (
            "namespace App;\npublic class Out {\n"
            "  public Cs Charset() => null;\n"
            "  public Tag Charset(string name) => null;\n"
            f"  public {second_returns} Charset(Out other) => null;\n"
            "  public string Use(string x) => Charset(x).Label();\n"
            "}\n"
        )
        edges = _edges(tmp_path, {"Cs.cs": ("csharp", types), "Out.cs": ("csharp", holder)})
        # Charset() returns Cs, but Charset(x) is not Charset(). The tied
        # members type the chain only when they agree; the first one's type
        # alone (Tag) is not taken.
        assert (("Out.cs::Out::Use", "Cs.cs::Tag::Label") in edges) is typed


class TestJavaLiteralArguments:
    """Literal arguments break a same-arity tie when they fit one member closest."""

    _ATTR = (
        "package app;\npublic class Attr {\n"
        "  public Attr value(String s) { return this; }\n"
        "  public Attr value(boolean b) { return this; }\n"
        "  public Attr value(int i) { return this; }\n"
        "  public Attr value(Object o) { return this; }\n"
        "  void use(Attr a) {\n    a.value(ARG);\n  }\n}\n"
    )

    @pytest.mark.parametrize(
        ("argument", "member"),
        [
            ("true", "value#1(boolean)"),
            ('"x"', "value#1(String)"),
            ("-1", "value#1(int)"),
            ("'c'", "value#1(int)"),
            ("1L", "value#1(Object)"),
            # Every reference parameter takes null: still a tie, kept on the first.
            ("null", "value#1(String)"),
        ],
    )
    def test_the_closest_member_takes_the_literal(
        self, tmp_path: Path, argument: str, member: str
    ) -> None:
        text = self._ATTR.replace("ARG", argument)
        edges = _edges(tmp_path, {"app/Attr.java": ("java", text)})
        assert edges == {("app/Attr.java::Attr::use", f"app/Attr.java::Attr::{member}")}

    def test_literal_types_are_read_per_argument(self) -> None:
        text = (
            "class A {\n  void go(int x) {\n"
            '    f(true, 2, 3L, 1.5, 1.5f, \'c\', "s", null, -1, x);\n  }\n}\n'
        )
        (call,) = _parse("A.java", "java", text).calls
        assert call.argument_types == (
            "boolean", "int", "long", "double", "float", "char", "String", "null", "int", None
        )

    def test_a_unary_operator_promotes_its_literal(self) -> None:
        text = (
            "class A {\n  void go() {\n"
            "    f(-'a', +'a', ~'a', ~1L, !true, -1.5f, !1, -\"s\");\n  }\n}\n"
        )
        (call,) = _parse("A.java", "java", text).calls
        assert call.argument_types == ("int", "int", "int", "long", "boolean", "float", None, None)

    def test_a_tie_that_differs_on_a_non_literal_argument_is_not_broken(
        self, tmp_path: Path
    ) -> None:
        """``value(obj, 1)``: the literal fits ``(String, int)`` better, but ``obj`` decides."""
        text = (
            "package app;\npublic class V {\n"
            "  public V value(Object o, long l) { return this; }\n"
            "  public V value(String s, int i) { return this; }\n"
            "  void use(Object obj) {\n    value(obj, 1);\n  }\n}\n"
        )
        edges = _edges(tmp_path, {"app/V.java": ("java", text)})
        # The tie stays on the first declared member, as before literals were read.
        assert edges == {("app/V.java::V::use", "app/V.java::V::value#2(Object,long)")}

    def test_a_varargs_array_takes_the_trailing_arguments(self, tmp_path: Path) -> None:
        """``add(0, node)``: both members take ``node`` as an ``N``, so the literal decides."""
        text = (
            "package app;\npublic class N {\n"
            "  public void add(N... nodes) { }\n"
            "  public void add(int at, N... nodes) { }\n"
            "  void use(N node) {\n    add(0, node);\n  }\n}\n"
        )
        edges = _edges(tmp_path, {"app/N.java": ("java", text)})
        assert edges == {("app/N.java::N::use", "app/N.java::N::add#2")}

    @pytest.mark.parametrize(
        ("call", "member"),
        [
            ("f(1)", "f#1(int[])"),
            ('f("a", "b")', "f#1(String[])"),
            ('g("a", 1, 2)', "g#2(String,int[])"),
        ],
    )
    def test_a_literal_through_varargs_is_judged_against_the_element_type(
        self, tmp_path: Path, call: str, member: str
    ) -> None:
        text = (
            "package app;\npublic class V {\n"
            "  public void f(String... xs) { }\n  public void f(int... xs) { }\n"
            "  public void g(String s, String... xs) { }\n  public void g(String s, int... xs) { }\n"
            f"  void use() {{\n    {call};\n  }}\n}}\n"
        )
        edges = _edges(tmp_path, {"app/V.java": ("java", text)})
        assert edges == {("app/V.java::V::use", f"app/V.java::V::{member}")}


class TestJavaNamedArguments:
    """A named argument's declared type breaks a tie the way a literal's does."""

    _MATH = (
        "package app;\npublic class M {\n"
        "  public static int mul(int a, int b) { return a; }\n"
        "  public static long mul(long a, int b) { return a; }\n"
        "  public static long mul(long a, long b) { return a; }\n"
        "BODY}\n"
    )

    @pytest.mark.parametrize(
        ("body", "member"),
        [
            # A parameter and a local.
            ("  long use(long total) {\n    int n = 2;\n    return mul(total, n);\n  }\n",
             "mul#2(long,int)"),
            # A field, read where the body declares no such name.
            ("  private long base;\n  long use(int n) {\n    return mul(base, n);\n  }\n",
             "mul#2(long,int)"),
            # A local of a primitive type stands over a field of the same name.
            ("  private long n;\n  long use(int a) {\n    int n = 1;\n    return mul(a, n);\n  }\n",
             "mul#2(int,int)"),
        ],
        ids=["parameter-and-local", "field", "local-over-field"],
    )
    def test_declared_types_choose_the_member(
        self, tmp_path: Path, body: str, member: str
    ) -> None:
        text = self._MATH.replace("BODY", body)
        edges = _edges(tmp_path, {"app/M.java": ("java", text)})
        assert edges == {("app/M.java::M::use", f"app/M.java::M::{member}")}

    def test_a_local_the_scan_cannot_type_hides_the_field(self, tmp_path: Path) -> None:
        """``var n = ...`` shadows the field ``n``: its type is unknown, so the tie stands."""
        body = "  private long n;\n  long use(int a) {\n    var n = a;\n    return mul(n, a);\n  }\n"
        edges = _edges(tmp_path, {"app/M.java": ("java", self._MATH.replace("BODY", body))})
        assert edges == {("app/M.java::M::use", "app/M.java::M::mul#2(int,int)")}

    def test_a_constructor_takes_its_arguments_declared_types(self, tmp_path: Path) -> None:
        """``new Span(start, end, kind, zone)`` beside ``Span(int, int, int, int)``."""
        text = (
            "package app;\npublic class Span {\n"
            "  public Span(int a, int b, int c, int d) { }\n"
            "  public Span(long start, long end, Kind kind, Zone zone) { }\n"
            "  static Span of(long start, long end, Kind kind, Zone zone) {\n"
            "    return new Span(start, end, kind, zone);\n  }\n}\n"
            "class Kind { }\nclass Zone { }\n"
        )
        edges = _edges(tmp_path, {"app/Span.java": ("java", text)})
        base = "app/Span.java::Span::"
        assert (base + "of", base + "Span#4(long,long,Kind,Zone)") in edges
        assert (base + "of", base + "Span#4(int,int,int,int)") not in edges

    def test_an_array_takes_the_array_member(self, tmp_path: Path) -> None:
        text = (
            "package app;\npublic class A {\n"
            "  public void f(String s) { }\n  public void f(String[] xs) { }\n"
            "  void use(String[] names) {\n    f(names);\n  }\n}\n"
        )
        edges = _edges(tmp_path, {"app/A.java": ("java", text)})
        assert edges == {("app/A.java::A::use", "app/A.java::A::f#1(String[])")}

    def test_argument_names_are_read_per_argument(self) -> None:
        text = 'class A {\n  void go(int x) {\n    f(x, 1, this.y, "s", z);\n  }\n}\n'
        (call,) = _parse("A.java", "java", text).calls
        assert call.argument_names == ("x", None, None, None, "z")
        assert call.argument_types == (None, "int", None, "String", None)

    @pytest.mark.parametrize(
        "members",
        [
            "  public void f(Object o) { }\n  public void f(Object[] os) { }\n",
            "  public void f(Object[] os) { }\n  public void f(Object o) { }\n",
        ],
        ids=["object-first", "array-first"],
    )
    def test_an_array_reaches_a_covariant_array_before_object(
        self, tmp_path: Path, members: str
    ) -> None:
        text = (
            f"package app;\npublic class A {{\n{members}"
            "  void use(String[] names) {\n    f(names);\n  }\n}\n"
        )
        edges = _edges(tmp_path, {"app/A.java": ("java", text)})
        assert edges == {("app/A.java::A::use", "app/A.java::A::f#1(Object[])")}

    def test_a_named_primitive_never_takes_an_array(self, tmp_path: Path) -> None:
        text = (
            "package app;\npublic class A {\n"
            "  public void f(int[] xs) { }\n  public void f(long x) { }\n"
            "  void use(int n) {\n    f(n);\n  }\n}\n"
        )
        edges = _edges(tmp_path, {"app/A.java": ("java", text)})
        assert edges == {("app/A.java::A::use", "app/A.java::A::f#1(long)")}

    def test_a_varargs_parameter_is_its_array_over_a_field(self, tmp_path: Path) -> None:
        text = (
            "package app;\npublic class A {\n  private long xs;\n"
            "  public void f(long x) { }\n  public void f(int[] xs) { }\n"
            "  void use(int... xs) {\n    f(xs);\n  }\n}\n"
        )
        edges = _edges(tmp_path, {"app/A.java": ("java", text)})
        assert edges == {("app/A.java::A::use", "app/A.java::A::f#1(int[])")}

    @pytest.mark.parametrize(
        ("members", "member"),
        [
            # Phase one takes Integer as an Object, with no unboxing.
            ("  void f(int i) { }\n  void f(long l) { }\n  void f(Object o) { }\n",
             "f#1(Object)"),
            # Unboxed to int before widened to long.
            ("  void f(long l) { }\n  void f(int i) { }\n", "f#1(int)"),
        ],
        ids=["object", "unboxed"],
    )
    def test_a_boxed_argument(self, tmp_path: Path, members: str, member: str) -> None:
        text = (
            f"package app;\npublic class A {{\n{members}"
            "  void use(Integer boxed) {\n    f(boxed);\n  }\n}\n"
        )
        edges = _edges(tmp_path, {"app/A.java": ("java", text)})
        assert edges == {("app/A.java::A::use", f"app/A.java::A::{member}")}

    @pytest.mark.parametrize(
        "lambda_", ["n -> mul(n, 2)", "(n) -> mul(n, 2)", "(a, n) -> mul(n, 2)"]
    )
    def test_a_lambda_parameter_hides_the_field(self, tmp_path: Path, lambda_: str) -> None:
        """Read as the ``long`` field, ``n`` would pick ``mul(long, int)``.

        A lambda parameter is a binding the scan reads, so ``n`` stays untyped
        and the tie stands.
        """
        body = (
            "  private long n;\n"
            f"  void use(java.util.function.IntConsumer c) {{\n    c = {lambda_};\n  }}\n"
        )
        edges = _edges(tmp_path, {"app/M.java": ("java", self._MATH.replace("BODY", body))})
        assert edges == {("app/M.java::M::use", "app/M.java::M::mul#2(int,int)")}

    def test_an_instanceof_pattern_variable_is_typed(self, tmp_path: Path) -> None:
        text = (
            "package app;\npublic class Shape {\n"
            "  public void f(Object o) { }\n  public void f(Shape s) { }\n"
            "  void use(Object o, boolean ok) {\n"
            "    if (o instanceof Shape s && ok) {\n      f(s);\n    }\n  }\n}\n"
        )
        edges = _edges(tmp_path, {"app/Shape.java": ("java", text)})
        assert edges == {("app/Shape.java::Shape::use", "app/Shape.java::Shape::f#1(Shape)")}

    @pytest.mark.parametrize(
        ("loop", "member"),
        [("for (long t : ts)", "mul#2(long,int)"), ("for (var t : ts)", "mul#2(int,int)")],
        ids=["typed", "var"],
    )
    def test_a_for_each_variable(self, tmp_path: Path, loop: str, member: str) -> None:
        """A typed loop variable is read; a ``var`` one hides the ``long`` field ``t``."""
        body = f"  private long t;\n  void use(long[] ts) {{\n    {loop} mul(t, 2);\n  }}\n"
        edges = _edges(tmp_path, {"app/M.java": ("java", self._MATH.replace("BODY", body))})
        assert edges == {("app/M.java::M::use", f"app/M.java::M::{member}")}

    def test_a_call_on_an_array_element_keeps_its_edge(self, tmp_path: Path) -> None:
        """An array is a builtin type now; ``items[0].m()`` names no receiver and still binds."""
        files = {
            "app/B.java": ("java", "package app;\npublic class B {\n  public void m() { }\n}\n"),
            "app/A.java": (
                "java",
                "package app;\npublic class A {\n"
                "  void use(B[] items) {\n    items[0].m();\n  }\n}\n",
            ),
        }
        assert ("app/A.java::A::use", "app/B.java::B::m") in _edges(tmp_path, files)

    @pytest.mark.parametrize(
        "members",
        [
            "  void add(Node... ns) { }\n  void add(int at, Node... ns) { }\n",
            "  void add(int at, Node... ns) { }\n  void add(Node... ns) { }\n",
        ],
        ids=["shorter-first", "longer-first"],
    )
    def test_an_array_alone_in_the_varargs_position_is_passed_whole(
        self, tmp_path: Path, members: str
    ) -> None:
        """``add(index, nodes)`` with ``Node... nodes`` passes the array, not one ``Node``."""
        text = (
            f"package app;\npublic class Node {{\n{members}"
            "  void use(int index, Node... nodes) {\n    add(index, nodes);\n  }\n}\n"
        )
        edges = _edges(tmp_path, {"app/Node.java": ("java", text)})
        assert edges == {("app/Node.java::Node::use", "app/Node.java::Node::add#2")}
