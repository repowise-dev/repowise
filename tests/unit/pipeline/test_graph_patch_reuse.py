"""Work the graph build does once and shares: warmups, context inputs, the spelled-name scan."""

from __future__ import annotations

import gc
from pathlib import Path
from types import SimpleNamespace

import networkx as nx
import pytest

from repowise.core.ingestion import GraphBuilder, graph_warmups
from repowise.core.pipeline.incremental import build_repo_graph
from repowise.core.pipeline.incremental.affected import _spells, _word_pattern


def _parsed(language: str) -> SimpleNamespace:
    return SimpleNamespace(file_info=SimpleNamespace(language=language))


def test_a_warmup_two_languages_share_runs_once_per_context(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def warmup(ctx: object) -> None:
        calls.append(ctx)

    monkeypatch.setattr(
        graph_warmups,
        "_WARMUPS",
        {"typescript": ("graph.ts_index", warmup), "javascript": ("graph.ts_index", warmup)},
    )
    parsed = {"a.ts": _parsed("typescript"), "b.js": _parsed("javascript")}
    ctx, other = SimpleNamespace(), SimpleNamespace()

    graph_warmups.run_warmups(parsed, ctx)
    graph_warmups.run_warmups({"b.js": parsed["b.js"]}, ctx)
    graph_warmups.run_warmups(parsed, other)

    assert calls == [ctx, other]


@pytest.mark.parametrize(
    ("text", "names", "stems", "expected"),
    [
        (b"call Widget();", {"Widget"}, (), True),
        (b"call MyWidget();", {"Widget"}, (), False),
        (b"call Widgets();", {"Widget"}, (), False),
        (b"$Widget", {"Widget"}, (), False),
        (b"Widget", {"Widget"}, (), True),
        (b"#include \"Widget_Impl.h\"", set(), ("widget_impl",), True),
        (b"WIDGET_IMPL", set(), ("widget_impl",), True),
        (b"xwidget_impl", set(), ("widget_impl",), False),
        # A rejected match does not hide a later word inside it.
        (b"xfoo-bar", {"foo-bar", "bar"}, (), True),
        (b"xfoo-bar", {"foo-bar"}, (), False),
    ],
)
def test_spelled_names_are_whole_words(
    text: bytes, names: set[str], stems: tuple[str, ...], expected: bool
) -> None:
    assert _spells(text, _word_pattern(names), _word_pattern(stems, fold=True)) is expected


def test_a_file_with_no_text_is_assumed_to_spell_the_name() -> None:
    assert _spells(None, _word_pattern({"Widget"}), None) is True


def test_resolver_contexts_share_inputs_until_a_file_is_added(tmp_path: Path) -> None:
    from repowise.core.ingestion.models import FileInfo, ParsedFile

    def parsed(path: str) -> ParsedFile:
        info = FileInfo(
            path=path,
            abs_path=str(tmp_path / path),
            language="python",
            size_bytes=0,
            git_hash="",
            last_modified=None,
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        return ParsedFile(file_info=info, symbols=[], imports=[], exports=[], docstring=None, parse_errors=[])

    builder = GraphBuilder(tmp_path)
    builder.add_file(parsed("pkg/a.py"))
    first = builder.resolver_context(nx.DiGraph())
    second = builder.resolver_context(nx.DiGraph())
    assert second.stem_map is first.stem_map
    assert second.path_set == first.path_set and second.path_set is not first.path_set

    builder.add_file(parsed("pkg/b.py"))
    third = builder.resolver_context(nx.DiGraph())
    assert third.stem_map is not first.stem_map
    assert "pkg/b.py" in third.path_set


def test_the_graph_build_leaves_the_collector_as_it_found_it(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("import b\n\ndef f():\n    return b.g()\n")
    (tmp_path / "b.py").write_text("def g():\n    return 1\n")

    build_repo_graph(tmp_path, [], collect_sources=True, read_only=True)
    assert gc.isenabled()

    gc.disable()
    try:
        build_repo_graph(tmp_path, [], collect_sources=True, read_only=True)
        assert not gc.isenabled()
    finally:
        gc.enable()


def test_the_call_resolver_reuses_the_warmed_cpp_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.ingestion.resolvers import cpp_workspace

    (tmp_path / "CMakeLists.txt").write_text("add_library(core a.cpp b.cpp)\n")
    (tmp_path / "a.cpp").write_text("int run() { return helper(); }\n")
    (tmp_path / "b.cpp").write_text("int helper() { return 1; }\n")
    built = []
    real = cpp_workspace.build_cpp_workspace_index

    def counting(ctx: object) -> object:
        built.append(ctx)
        return real(ctx)

    monkeypatch.setattr(cpp_workspace, "build_cpp_workspace_index", counting)

    graph = build_repo_graph(tmp_path, [], collect_sources=True, read_only=True)[2].graph()

    assert len(built) == 1
    assert graph.has_edge("a.cpp::run", "b.cpp::helper")
