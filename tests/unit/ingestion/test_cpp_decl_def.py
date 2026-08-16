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
