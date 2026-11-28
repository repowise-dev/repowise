"""C++ calls written through a type name: function-style casts, constructions and aliases.

``T(x)`` binds the class, a class and its own constructors are one target for
the repo-wide tier, and a cast through an alias of a non-class or library type
draws no edge.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_PIECE = """#pragma once
#include <string>
struct Piece {
  Piece() {}
  Piece(const std::string& s) {}
  Piece(const char* s) {}
  Piece(const char* s, int n) {}
};
"""


def _build(repo: Path):
    traverser = FileTraverser(repo)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in traverser.traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


def _callees_of(graph, symbol_id: str) -> set[str]:
    return {
        succ
        for succ in graph.successors(symbol_id)
        if graph[symbol_id][succ].get("edge_type") == "calls"
    }


def test_one_argument_cast_binds_the_class_not_its_constructor(tmp_path: Path) -> None:
    (tmp_path / "slot.h").write_text(
        "struct Slot {\n  Slot() = default;\n  explicit Slot(int v) : v_(v) {}\n"
        "  static Slot Make(int v) { return Slot(v); }\n  int v_;\n};\n"
    )
    graph = _build(tmp_path)
    assert _callees_of(graph, "slot.h::Slot::Make") == {"slot.h::Slot"}


def test_one_argument_cast_through_an_unincluded_header_binds_the_class(tmp_path: Path) -> None:
    # Two one-argument constructors used to read as an ambiguity with the class.
    (tmp_path / "piece.h").write_text(_PIECE)
    (tmp_path / "graph.cc").write_text("void Use(const char* p) { Piece a = Piece(p); }\n")
    graph = _build(tmp_path)
    assert _callees_of(graph, "graph.cc::Use") == {"piece.h::Piece"}


def test_two_argument_construction_binds_the_constructor(tmp_path: Path) -> None:
    (tmp_path / "piece.h").write_text(_PIECE)
    (tmp_path / "lexer.cc").write_text("void Lex(const char* p) { Piece a = Piece(p, 3); }\n")
    graph = _build(tmp_path)
    (callee,) = _callees_of(graph, "lexer.cc::Lex")
    assert graph.nodes[callee]["kind"] == "method"
    assert graph.nodes[callee]["start_line"] == 7


def test_cast_through_a_non_class_alias_is_not_a_call(tmp_path: Path) -> None:
    (tmp_path / "map.h").write_text(
        "#include <cstdint>\n#include <utility>\n"
        "struct Map {\n  using size_type = uint32_t;\n"
        "  using value_type = std::pair<int, int>;\n"
        "  size_type Slot(unsigned long h) { return size_type(h); }\n"
        "  value_type Pair() { return value_type(); }\n};\n"
    )
    graph = _build(tmp_path)
    assert _callees_of(graph, "map.h::Map::Slot") == set()
    assert _callees_of(graph, "map.h::Map::Pair") == set()


def test_cast_through_an_alias_of_a_repo_class_keeps_the_alias(tmp_path: Path) -> None:
    (tmp_path / "view.h").write_text(
        "template <typename C> class basic_view {\n public:\n"
        "  basic_view(const C* p) : p_(p) {}\n  const C* p_;\n};\n"
        "using view = basic_view<char>;\n"
    )
    (tmp_path / "use.cc").write_text('#include "view.h"\nvoid Show() { auto v = view("x"); }\n')
    graph = _build(tmp_path)
    assert _callees_of(graph, "use.cc::Show") == {"view.h::view"}


def test_a_name_local_to_another_files_function_is_no_rival(tmp_path: Path) -> None:
    (tmp_path / "view.h").write_text("struct text {};\nusing view = text;\n")
    (tmp_path / "other_test.cc").write_text(
        "void Case() {\n  struct local {\n    using view = int;\n  };\n}\n"
    )
    (tmp_path / "use.cc").write_text("void Show() { auto v = view(); }\n")
    graph = _build(tmp_path)
    assert _callees_of(graph, "use.cc::Show") == {"view.h::view"}


def test_alias_chains_and_inline_struct_typedefs(tmp_path: Path) -> None:
    (tmp_path / "types.h").write_text(
        "struct base {};\nusing mid = base;\nusing top = mid;\n"
        "typedef struct {\n  int x;\n} point;\n"
        "typedef struct {\n  int y;\n} *point_ptr;\n"
        "struct type {};\n"
        "template <typename C> struct impl { using type = C; };\n"
        "template <typename C> using member = typename impl<C>::type;\n"
    )
    (tmp_path / "use.cc").write_text(
        '#include "types.h"\n'
        "void Chain() { auto t = top(); }\n"
        "void Inline() { auto p = point(); }\n"
        "void Pointer(void* v) { auto p = point_ptr(v); }\n"
        "void Member(int v) { auto m = member<int>(v); }\n"
    )
    graph = _build(tmp_path)
    assert _callees_of(graph, "use.cc::Chain") == {"types.h::top"}
    assert _callees_of(graph, "use.cc::Inline") == {"types.h::point"}
    assert _callees_of(graph, "use.cc::Pointer") == set()
    assert _callees_of(graph, "use.cc::Member") == set()
