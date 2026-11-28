"""C++ calls written through a type name: function-style casts, constructions and aliases.

A construction binds the one user constructor its argument count fits (none on
a tie or when none fits), and a cast through an alias of a non-class or library
type draws no edge.
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


def _callee_lines(graph, symbol_id: str) -> set[tuple[str, int]]:
    return {
        (graph.nodes[c]["kind"], graph.nodes[c]["start_line"])
        for c in _callees_of(graph, symbol_id)
    }


def test_one_argument_construction_binds_the_constructor_that_fits(tmp_path: Path) -> None:
    (tmp_path / "slot.h").write_text(
        "struct Slot {\n  Slot() = default;\n  explicit Slot(int v) : v_(v) {}\n"
        "  static Slot Make(int v) { return Slot(v); }\n  int v_;\n};\n"
    )
    graph = _build(tmp_path)
    assert _callee_lines(graph, "slot.h::Slot::Make") == {("method", 3)}


def test_a_tie_between_constructors_of_one_arity_draws_no_edge(tmp_path: Path) -> None:
    (tmp_path / "piece.h").write_text(_PIECE)
    (tmp_path / "graph.cc").write_text("void Use(const char* p) { Piece a = Piece(p); }\n")
    graph = _build(tmp_path)
    assert _callees_of(graph, "graph.cc::Use") == set()


def test_two_argument_construction_binds_the_constructor(tmp_path: Path) -> None:
    (tmp_path / "piece.h").write_text(_PIECE)
    (tmp_path / "lexer.cc").write_text("void Lex(const char* p) { Piece a = Piece(p, 3); }\n")
    graph = _build(tmp_path)
    assert _callee_lines(graph, "lexer.cc::Lex") == {("method", 7)}


def test_construction_through_a_forward_declared_class_binds_by_argument_count(
    tmp_path: Path,
) -> None:
    # The included header only forward-declares the class; its body is elsewhere.
    (tmp_path / "piece.h").write_text(_PIECE)
    (tmp_path / "fwd.h").write_text(
        "#pragma once\nstruct Piece;\nstruct Options {\n  Options() : n(0) {}\n  int n;\n};\n"
    )
    (tmp_path / "put.cc").write_text(
        '#include "fwd.h"\n'
        "void Put(const char* k, int n) { Piece a = Piece(k, n); }\n"
        "void Empty() { Piece a = Piece(); }\n"
        "void Opts() { Options o = Options(); }\n"
    )
    graph = _build(tmp_path)
    assert _callee_lines(graph, "put.cc::Put") == {("method", 7)}
    assert _callee_lines(graph, "put.cc::Empty") == {("method", 4)}
    assert _callee_lines(graph, "put.cc::Opts") == {("method", 4)}


def test_no_fitting_constructor_draws_no_edge(tmp_path: Path) -> None:
    # Neither the class node nor a constructor of another arity is what ran.
    (tmp_path / "types.h").write_text(
        "#pragma once\nstruct Plain {\n  int a;\n  int b;\n};\n"
        "struct Vec {\n  Vec() {}\n  Vec(float x, float y, float z) {}\n};\n"
    )
    (tmp_path / "use.cc").write_text(
        '#include "types.h"\n'
        "void Make() { Plain p = Plain(); }\n"
        "void Two(float x) { Vec v = Vec(x, x); }\n"
        "void Three(float x) { Vec v = Vec(x, x, x); }\n"
    )
    graph = _build(tmp_path)
    assert _callees_of(graph, "use.cc::Make") == set()
    assert _callees_of(graph, "use.cc::Two") == set()
    assert _callee_lines(graph, "use.cc::Three") == {("method", 8)}


def test_default_arguments_admit_a_shorter_construction(tmp_path: Path) -> None:
    (tmp_path / "color.h").write_text(
        "#pragma once\nstruct Color {\n  Color() {}\n  Color(int r, int g, int b, int a = 255) {}\n};\n"
    )
    (tmp_path / "use.cc").write_text(
        '#include "color.h"\nvoid Red() { Color c = Color(255, 0, 0); }\n'
    )
    graph = _build(tmp_path)
    assert _callee_lines(graph, "use.cc::Red") == {("method", 4)}


def test_defaults_on_the_declaration_count_for_an_out_of_line_constructor(tmp_path: Path) -> None:
    (tmp_path / "slice.h").write_text(
        "#pragma once\nstruct Slice {\n  Slice(const char* s, int n = 0);\n  const char* d;\n};\n"
        "inline Slice::Slice(const char* s, int n) : d(s) {}\n"
    )
    (tmp_path / "use.cc").write_text(
        '#include "slice.h"\nvoid One() { Slice s = Slice("x"); }\n'
        'void Two() { Slice s = Slice("x", 1); }\n'
    )
    graph = _build(tmp_path)
    assert _callees_of(graph, "use.cc::One") == {"slice.h::Slice::Slice"}
    assert _callees_of(graph, "use.cc::Two") == {"slice.h::Slice::Slice"}


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
    # ``typedef struct {..} point`` declares a class with no constructor to call.
    assert _callees_of(graph, "use.cc::Inline") == set()
    assert _callees_of(graph, "use.cc::Pointer") == set()
    assert _callees_of(graph, "use.cc::Member") == set()


def test_a_nested_class_construction_binds_its_constructor(tmp_path: Path) -> None:
    (tmp_path / "pool.h").write_text(
        "#pragma once\nstruct Pool {\n  struct Slot {\n    Slot() = default;\n"
        "    Slot(int v, int w) {}\n  };\n};\n"
    )
    (tmp_path / "use.cc").write_text(
        '#include "pool.h"\nPool::Slot Make() { return Pool::Slot(1, 2); }\n'
        "Pool::Slot Empty() { return Pool::Slot(); }\n"
    )
    graph = _build(tmp_path)
    assert _callee_lines(graph, "use.cc::Make") == {("method", 5)}
    assert _callee_lines(graph, "use.cc::Empty") == {("method", 4)}
