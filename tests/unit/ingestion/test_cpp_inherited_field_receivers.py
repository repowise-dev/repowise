"""A C++ receiver typed by a field a base class declares.

``lexer_.ReadToken()`` in a parser derived from ``Parser`` reads the base's
``Lexer lexer_``. A ``struct`` base is a class base: the two differ only in
default access.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


def _callees(root: Path, caller: str) -> set[str]:
    builder = GraphBuilder(repo_path=root)
    parser = ASTParser()
    for fi in FileTraverser(root).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    graph = builder.build()
    return {
        t for s, t, d in graph.edges(data=True) if d.get("edge_type") == "calls" and s == caller
    }


_LEXER_H = "#pragma once\nstruct Lexer {\n  int ReadToken();\n};\n"
_LEXER_CC = '#include "lexer.h"\nint Lexer::ReadToken() {\n  return 1;\n}\n'
_OTHER_H = "#pragma once\nstruct Other {\n  int ReadToken();\n};\n"
_OTHER_CC = '#include "other.h"\nint Other::ReadToken() {\n  return 2;\n}\n'


def _repo(root: Path, base_kind: str, child: str) -> None:
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "lexer.h").write_text(_LEXER_H)
    (src / "lexer.cc").write_text(_LEXER_CC)
    (src / "other.h").write_text(_OTHER_H)
    (src / "other.cc").write_text(_OTHER_CC)
    (src / "parser.h").write_text(
        f'#pragma once\n#include "lexer.h"\n{base_kind} Parser {{\n'
        " protected:\n  Lexer lexer_;\n};\n"
    )
    (src / "child.h").write_text(child)
    (src / "child.cc").write_text(
        '#include "child.h"\nbool Child::Parse() {\n  return lexer_.ReadToken() > 0;\n}\n'
    )


_CHILD_H = (
    '#pragma once\n#include "parser.h"\nstruct Child : public Parser {\n  bool Parse();\n};\n'
)


class TestCppInheritedFieldReceiver:
    def test_a_struct_base_field_types_the_receiver(self, tmp_path: Path) -> None:
        _repo(tmp_path, "struct", _CHILD_H)
        callees = _callees(tmp_path, "src/child.cc::Child::Parse")
        assert callees == {"src/lexer.cc::Lexer::ReadToken"}

    def test_a_class_base_field_types_the_receiver(self, tmp_path: Path) -> None:
        _repo(tmp_path, "class", _CHILD_H)
        callees = _callees(tmp_path, "src/child.cc::Child::Parse")
        assert callees == {"src/lexer.cc::Lexer::ReadToken"}

    def test_an_own_field_hides_the_base_one(self, tmp_path: Path) -> None:
        _repo(
            tmp_path,
            "struct",
            '#pragma once\n#include "parser.h"\n#include "other.h"\n'
            "struct Child : public Parser {\n  bool Parse();\n  Other lexer_;\n};\n",
        )
        callees = _callees(tmp_path, "src/child.cc::Child::Parse")
        assert callees == {"src/other.cc::Other::ReadToken"}


_SPECS_H = (
    "#pragma once\n"
    "class basic_specs {\n public:\n  void set_alt() { data_ |= 1; }\n"
    "  void set_type(int t) { data_ = t; }\n private:\n  int data_;\n};\n"
    "struct format_specs : basic_specs {\n  int width;\n};\n"
    "template <typename Char = char> struct dynamic_format_specs : format_specs {\n"
    "  int width_ref;\n};\n"
)


def _specs_repo(root: Path, body: str) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "specs.h").write_text(_SPECS_H)
    (root / "src" / "use.h").write_text(f'#pragma once\n#include "specs.h"\n{body}')


class TestCppTemplatedReceiverScopes:
    def test_a_local_class_field_leaves_the_parameter_typed(self, tmp_path: Path) -> None:
        _specs_repo(
            tmp_path,
            "template <typename Char>\n"
            "void parse(dynamic_format_specs<Char>& specs, int t) {\n"
            "  struct {\n    format_specs& specs;\n"
            "    void operator()(int s) { specs.set_type(s); }\n"
            "  } handler{specs};\n"
            "  specs.set_alt();\n}\n",
        )
        callees = _callees(tmp_path, "src/use.h::parse")
        assert callees == {"src/specs.h::basic_specs::set_alt"}

    def test_a_partial_specialization_types_its_own_field(self, tmp_path: Path) -> None:
        _specs_repo(
            tmp_path,
            "template <typename T, typename Char = char> struct formatter;\n"
            "template <typename Char> struct formatter<int, Char> {\n"
            " private:\n  format_specs specs_;\n"
            " public:\n  void set() { specs_.set_alt(); }\n};\n",
        )
        callees = _callees(tmp_path, "src/use.h::formatter<int, Char>::set")
        assert callees == {"src/specs.h::basic_specs::set_alt"}

    def test_a_specialization_is_a_class_its_members_hang_on(self, tmp_path: Path) -> None:
        _specs_repo(
            tmp_path,
            "template <typename T> struct formatter;\n"
            "template <> struct formatter<\n    long> {\n  void set();\n};\n",
        )
        builder = GraphBuilder(repo_path=tmp_path)
        parser = ASTParser()
        for fi in FileTraverser(tmp_path).traverse():
            builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
        graph = builder.build()
        assert graph.nodes["src/use.h::formatter<long>"]["kind"] == "struct"
        assert graph.has_edge("src/use.h::formatter<long>", "src/use.h::formatter<long>::set")

    def test_a_local_on_the_field_line_keeps_the_name_ambiguous(self, tmp_path: Path) -> None:
        _specs_repo(
            tmp_path,
            "struct Other {\n  void other_m();\n};\n"
            "void g(format_specs x) {\n  {\n"
            "    struct S { Other x; }; S x;\n    x.set_alt();\n  }\n}\n",
        )
        assert _callees(tmp_path, "src/use.h::g") == set()
