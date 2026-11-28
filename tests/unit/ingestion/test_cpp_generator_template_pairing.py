"""A C/C++ declaration pairs with the generated source, not the generator's template.

A lexer generator reads ``lexer.in.cc`` and writes ``lexer.cc``; both define
``Lexer::ReadToken``, but only the generated file is compiled.
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


def _body(value: int) -> str:
    return (
        f'#include "lexer.h"\nint Lexer::ReadToken() {{\n  return {value};\n}}\n'
        f"int Scan() {{\n  return {value};\n}}\n"
    )


def _repo(root: Path, *, generated: bool) -> None:
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "lexer.h").write_text(
        "#pragma once\nstruct Lexer {\n  int ReadToken();\n};\nint Scan();\n"
    )
    (src / "lexer.in.cc").write_text(_body(1))
    if generated:
        (src / "lexer.cc").write_text(_body(2))
    (src / "use.cc").write_text(
        '#include "lexer.h"\nint Use(Lexer* lexer) {\n  return lexer->ReadToken() + Scan();\n}\n'
    )


class TestGeneratorTemplatePairing:
    def test_the_generated_source_holds_the_definitions(self, tmp_path: Path) -> None:
        _repo(tmp_path, generated=True)
        assert _callees(tmp_path, "src/use.cc::Use") == {
            "src/lexer.cc::Lexer::ReadToken",
            "src/lexer.cc::Scan",
        }

    def test_a_template_alone_still_pairs(self, tmp_path: Path) -> None:
        _repo(tmp_path, generated=False)
        assert _callees(tmp_path, "src/use.cc::Use") == {
            "src/lexer.in.cc::Lexer::ReadToken",
            "src/lexer.in.cc::Scan",
        }
