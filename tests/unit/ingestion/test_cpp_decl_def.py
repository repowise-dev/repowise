"""C/C++ forward declaration ↔ definition pairing in call resolution (#1601).

The standard C++ layout declares a function in a header and defines it in a
.cpp. Both land as same-named symbols, and every resolution tier below the
same-file one looks names up in the *header's* symbol table, because the
header is what a caller includes. The call edge therefore attached to the
declaration and the definition was left with no inbound edge at all, which
read as a ``safe_to_delete`` unused export.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


def _build(repo: Path):
    traverser = FileTraverser(repo)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in traverser.traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


def _callers_of(graph, symbol_id: str) -> set[str]:
    return {
        pred
        for pred in graph.predecessors(symbol_id)
        if graph[pred][symbol_id].get("edge_type") == "calls"
    }


class TestDeclarationFlag:
    def test_header_declaration_is_marked(self, tmp_path: Path) -> None:
        (tmp_path / "shapes.h").write_text("namespace Geo { double Area(double r); }\n")
        graph = _build(tmp_path)
        assert graph.nodes["shapes.h::Area"]["is_declaration"] is True

    def test_definition_is_not_marked(self, tmp_path: Path) -> None:
        (tmp_path / "shapes.cpp").write_text(
            "namespace Geo { double Area(double r) { return r; } }\n"
        )
        graph = _build(tmp_path)
        assert graph.nodes["shapes.cpp::Area"]["is_declaration"] is False

    def test_non_cpp_language_never_marked(self, tmp_path: Path) -> None:
        # ``declaration_node_types`` is empty for every other language, so the
        # flag stays off and the redirect below is a C/C++-only behaviour.
        (tmp_path / "mod.py").write_text("def area(r):\n    return r\n")
        graph = _build(tmp_path)
        assert graph.nodes["mod.py::area"]["is_declaration"] is False


class TestCallAttachesToDefinition:
    def test_cross_file_call_reaches_the_definition(self, tmp_path: Path) -> None:
        (tmp_path / "shapes.h").write_text("namespace Geo { double Area(double r); }\n")
        (tmp_path / "shapes.cpp").write_text(
            '#include "shapes.h"\n'
            "namespace Geo { double Area(double r) { return 3.14 * r * r; } }\n"
        )
        (tmp_path / "main.cpp").write_text(
            '#include "shapes.h"\nint main() { return (int)Geo::Area(2.0); }\n'
        )
        graph = _build(tmp_path)
        assert _callers_of(graph, "shapes.cpp::Area") == {"main.cpp::main"}
        # The declaration keeps its ``defines`` edge and nothing else.
        assert _callers_of(graph, "shapes.h::Area") == set()

    def test_c_translation_unit_pairing(self, tmp_path: Path) -> None:
        (tmp_path / "util.h").write_text("int helper(void);\n")
        (tmp_path / "util.c").write_text('#include "util.h"\nint helper(void) { return 1; }\n')
        (tmp_path / "app.c").write_text('#include "util.h"\nint run(void) { return helper(); }\n')
        graph = _build(tmp_path)
        assert _callers_of(graph, "util.c::helper") == {"app.c::run"}

    def test_same_file_forward_declaration(self, tmp_path: Path) -> None:
        # A .cpp that forward-declares a helper above its own body holds both
        # records; the declaration must not shadow the definition in the
        # file-level symbol index.
        (tmp_path / "solo.cpp").write_text(
            "static int helper(int);\n"
            "int run() { return helper(1); }\n"
            "static int helper(int v) { return v * 2; }\n"
        )
        graph = _build(tmp_path)
        assert "solo.cpp::run" in _callers_of(graph, "solo.cpp::helper")

    def test_paired_declaration_records_its_definition(self, tmp_path: Path) -> None:
        (tmp_path / "shapes.h").write_text("double Area(double r);\n")
        (tmp_path / "shapes.cpp").write_text(
            '#include "shapes.h"\ndouble Area(double r) { return r; }\n'
        )
        graph = _build(tmp_path)
        assert graph.nodes["shapes.h::Area"]["defined_by"] == "shapes.cpp::Area"

    def test_orphaned_prototype_records_no_definition(self, tmp_path: Path) -> None:
        (tmp_path / "orphan.h").write_text("double Vanished(double r);\n")
        graph = _build(tmp_path)
        assert graph.nodes["orphan.h::Vanished"].get("defined_by") is None

    def test_overload_set_across_files_is_left_alone(self, tmp_path: Path) -> None:
        # Two definitions of ``Area`` in different translation units, neither
        # including the header: the pairing is genuinely ambiguous, so no
        # redirect is invented.
        (tmp_path / "shapes.h").write_text("double Area(double r);\n")
        (tmp_path / "circle.cpp").write_text("double Area(double r) { return r; }\n")
        (tmp_path / "square.cpp").write_text("double Area(double r) { return r * r; }\n")
        graph = _build(tmp_path)
        assert _callers_of(graph, "circle.cpp::Area") == set()
        assert _callers_of(graph, "square.cpp::Area") == set()


class TestTypeDeclarationFlag:
    """``class Env;`` is a declaration too, and only the ``body`` field says so.

    Mechanics in ``parser._is_bodiless_cpp_type``.
    """

    def test_bodiless_type_is_marked(self, tmp_path: Path) -> None:
        (tmp_path / "builder.h").write_text(
            "namespace leveldb {\nclass Env;\nstruct Options;\nenum Level;\n}\n"
        )
        graph = _build(tmp_path)
        for name in ("Env", "Options", "Level"):
            assert graph.nodes[f"builder.h::{name}"]["is_declaration"] is True, name

    def test_type_with_a_body_is_not_marked(self, tmp_path: Path) -> None:
        (tmp_path / "env.h").write_text(
            "namespace leveldb {\nclass Env {\n public:\n  int Now();\n};\n}\n"
        )
        graph = _build(tmp_path)
        assert graph.nodes["env.h::Env"]["is_declaration"] is False

    def test_c_opaque_handle_typedef_stays_reportable(self, tmp_path: Path) -> None:
        # ``typedef struct Foo Foo;`` is C's opaque-handle idiom. Tag and
        # typedef name are one identifier, so the two query patterns match at
        # one position and a single symbol survives — the typedef name, which
        # is real API and must not be exempted as a forward declaration.
        (tmp_path / "ac.h").write_text(
            "typedef struct CBMAutomaton CBMAutomaton;\n"
            "typedef struct Impl_s Handle;\n"
            "struct Plain;\n"
        )
        graph = _build(tmp_path)
        assert graph.nodes["ac.h::CBMAutomaton"]["is_declaration"] is False
        assert graph.nodes["ac.h::Handle"]["is_declaration"] is False
        # Not a typedef, so still a plain forward declaration.
        assert graph.nodes["ac.h::Plain"]["is_declaration"] is True

    def test_definition_wins_whichever_order_they_appear_in(self, tmp_path: Path) -> None:
        # Declaration and definition share a symbol id, so the graph holds one
        # node and the last emission would otherwise win. Definition-*after*-
        # declaration passes either way; the reverse is the real case (seastar's
        # smp.hh defines smp_message_queue at 177 and re-declares it at 301) and
        # it silently reduced a 120-line class to a one-line declaration.
        decl, defn = "class Shape;", "class Shape {\n public:\n  int Area();\n};"
        for name, body in (
            ("decl_first.h", f"namespace geo {{\n{decl}\n{defn}\n}}\n"),
            ("def_first.h", f"namespace geo {{\n{defn}\n{decl}\n}}\n"),
        ):
            (tmp_path / name).write_text(body)
        graph = _build(tmp_path)
        for name in ("decl_first.h", "def_first.h"):
            node = graph.nodes[f"{name}::Shape"]
            assert node["is_declaration"] is False, name
            assert node["end_line"] > node["start_line"], name

    def test_the_definition_keeps_its_defines_edge(self, tmp_path: Path) -> None:
        # Skipping the declaration's ``add_node`` must not skip its file edge,
        # or the file stops defining a symbol it does define.
        (tmp_path / "def_first.h").write_text(
            "namespace geo {\nclass Shape {\n public:\n  int Area();\n};\nclass Shape;\n}\n"
        )
        graph = _build(tmp_path)
        assert graph.get_edge_data("def_first.h", "def_first.h::Shape") == {
            "edge_type": "defines"
        }

    def test_bodiless_template_type_is_marked(self, tmp_path: Path) -> None:
        # ``template <typename T> class Foo;`` — the wrapper node carries no
        # ``body`` field of its own, so the check has to ask the type it wraps
        # or every template reads as a declaration.
        (tmp_path / "t.hh").write_text(
            "template <typename T> class Foo;\n"
            "template <typename T> class Bar { int x; };\n"
            "template <typename... T>\nclass Multi;\n"
            "template <typename T> T fn() { return T(); }\n"
        )
        graph = _build(tmp_path)
        assert graph.nodes["t.hh::Foo"]["is_declaration"] is True
        assert graph.nodes["t.hh::Multi"]["is_declaration"] is True
        assert graph.nodes["t.hh::Bar"]["is_declaration"] is False
        assert graph.nodes["t.hh::fn"]["is_declaration"] is False
