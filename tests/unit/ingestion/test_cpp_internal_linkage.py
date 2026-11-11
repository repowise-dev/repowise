"""A C/C++ symbol with internal linkage is only callable from its own file.

A ``static`` function, or one in an anonymous namespace, cannot be linked from
another translation unit, so a bare call elsewhere must not bind to it even
when its name is unique in the repository.
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


def _callees_of(graph, symbol_id: str) -> set[str]:
    return {
        succ
        for succ in graph.successors(symbol_id)
        if graph[symbol_id][succ].get("edge_type") == "calls"
    }


def test_static_function_in_another_c_file_is_not_bound(tmp_path: Path) -> None:
    (tmp_path / "bench.c").write_text("static int make_client(void) { return 1; }\n")
    (tmp_path / "server.c").write_text("int start(void) { return make_client(); }\n")
    graph = _build(tmp_path)
    assert _callees_of(graph, "server.c::start") == set()


def test_static_function_does_not_hide_the_linkable_one(tmp_path: Path) -> None:
    (tmp_path / "bench.c").write_text("static int make_client(void) { return 1; }\n")
    (tmp_path / "net.c").write_text("int make_client(void) { return 2; }\n")
    (tmp_path / "server.c").write_text("int start(void) { return make_client(); }\n")
    graph = _build(tmp_path)
    assert _callees_of(graph, "server.c::start") == {"net.c::make_client"}


def test_anonymous_namespace_function_in_another_file_is_not_bound(tmp_path: Path) -> None:
    (tmp_path / "a.cpp").write_text("namespace { int Helper() { return 1; } }\n")
    (tmp_path / "b.cpp").write_text("int Run() { return Helper(); }\n")
    graph = _build(tmp_path)
    assert _callees_of(graph, "b.cpp::Run") == set()


def test_static_function_in_the_same_file_still_binds(tmp_path: Path) -> None:
    (tmp_path / "util.c").write_text(
        "static int helper(void) { return 1; }\nint run(void) { return helper(); }\n"
    )
    graph = _build(tmp_path)
    assert _callees_of(graph, "util.c::run") == {"util.c::helper"}


def test_non_static_function_in_another_file_still_binds(tmp_path: Path) -> None:
    (tmp_path / "util.c").write_text("int helper(void) { return 1; }\n")
    (tmp_path / "app.c").write_text("int run(void) { return helper(); }\n")
    graph = _build(tmp_path)
    assert _callees_of(graph, "app.c::run") == {"util.c::helper"}


def test_static_inline_in_a_header_still_binds(tmp_path: Path) -> None:
    # A header's static copy is compiled into every includer.
    (tmp_path / "atomic.hpp").write_text("static inline int load(int *p) { return *p; }\n")
    (tmp_path / "app.cpp").write_text("int run(int *p) { return load(p); }\n")
    graph = _build(tmp_path)
    assert _callees_of(graph, "app.cpp::run") == {"atomic.hpp::load"}


def test_static_objectivec_function_in_another_m_file_is_not_bound(tmp_path: Path) -> None:
    (tmp_path / "bench.m").write_text("static int make_client(void) { return 1; }\n")
    (tmp_path / "server.m").write_text("int start(void) { return make_client(); }\n")
    graph = _build(tmp_path)
    assert _callees_of(graph, "server.m::start") == set()


def test_static_objectivec_function_does_not_hide_the_linkable_one(tmp_path: Path) -> None:
    (tmp_path / "bench.m").write_text("static int make_client(void) { return 1; }\n")
    (tmp_path / "net.m").write_text("int make_client(void) { return 2; }\n")
    (tmp_path / "server.m").write_text("int start(void) { return make_client(); }\n")
    graph = _build(tmp_path)
    assert _callees_of(graph, "server.m::start") == {"net.m::make_client"}


def test_static_objectivec_function_in_the_same_m_file_still_binds(tmp_path: Path) -> None:
    (tmp_path / "util.m").write_text(
        "static int helper(void) { return 1; }\nint run(void) { return helper(); }\n"
    )
    graph = _build(tmp_path)
    assert _callees_of(graph, "util.m::run") == {"util.m::helper"}
