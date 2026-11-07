"""A Pascal symbol is used when its unit or an importing unit writes its name."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

pytest.importorskip("tree_sitter_pascal", reason="run `uv sync --all-packages`")

CONSOLE = """\
unit uConsole;
interface
type
  TConsoleBuffer = class
  public
    procedure Clear;
  end;
  TUnusedBuffer = class
  end;
function DescribeBuffer: string;
function UnusedHelper: string;
implementation
procedure TConsoleBuffer.Clear;
begin
end;
function DescribeBuffer: string;
begin
  Result := 'TUnusedBuffer';
end;
function UnusedHelper: string;
begin
  Result := 'UnusedHelper';
end;
end.
"""

MAIN = """\
program Main;
uses
  uConsole;
var
  Buf: TConsoleBuffer;
begin
  { UnusedHelper is mentioned only in this comment }
  WriteLn(describebuffer);
end.
"""


@pytest.fixture(scope="module")
def unused(tmp_path_factory: pytest.TempPathFactory) -> set[str]:
    repo = tmp_path_factory.mktemp("pascal_dead")
    (repo / "uConsole.pas").write_text(CONSOLE, encoding="utf-8")
    (repo / "Main.dpr").write_text(MAIN, encoding="utf-8")
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    source_map: dict[str, bytes] = {}
    for fi in FileTraverser(repo).traverse():
        raw = Path(fi.abs_path).read_bytes()
        source_map[fi.path] = raw
        builder.add_file(parser.parse_file(fi, raw))
    builder.set_source_map(source_map)
    graph = builder.build()
    report = DeadCodeAnalyzer(graph, git_meta_map={}, source_map=source_map).analyze(
        {"detect_unreachable_files": False, "detect_zombie_packages": False, "min_confidence": 0.0}
    )
    return {
        f.symbol_name
        for f in report.findings
        if f.kind == DeadCodeKind.UNUSED_EXPORT and f.symbol_name
    }


def test_a_type_named_only_in_a_declaration_is_used(unused: set[str]) -> None:
    assert "TConsoleBuffer" not in unused


def test_a_name_written_in_another_case_is_used(unused: set[str]) -> None:
    assert "DescribeBuffer" not in unused


def test_a_uses_clause_alone_does_not_use_every_symbol_of_the_unit(unused: set[str]) -> None:
    assert "TUnusedBuffer" in unused


def test_comments_strings_and_declarations_are_not_uses(unused: set[str]) -> None:
    assert "UnusedHelper" in unused
