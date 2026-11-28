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
