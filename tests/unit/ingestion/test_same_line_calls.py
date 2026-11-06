"""Calls to one name that share a line each keep their own call site.

A nested ``f(a, f(b))``, two statements on one line, and every link of a
builder chain (all of which report the chain's first line) are separate calls.
Each may bind a different overload, so the parser dedupes on the call node,
not the line, and the graph draws an edge to every overload reached.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.graph.builder import GraphBuilder
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


def _parse(rel: str, language: str, text: str, root: Path | None = None) -> ParsedFile:
    """Parse *text*; with *root*, also write it there, where receiver typing reads it."""
    abs_path = rel
    if root is not None:
        (root / rel).write_text(text, encoding="utf-8")
        abs_path = str(root / rel)
    info = FileInfo(
        path=rel,
        abs_path=abs_path,
        language=language,  # type: ignore[arg-type]
        size_bytes=len(text),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return _PARSER.parse_file(info, text.encode("utf-8"))


def _call_edges(root: Path, rel: str, language: str, text: str) -> set[tuple[str, str]]:
    builder = GraphBuilder(repo_path=root)
    builder.add_file(_parse(rel, language, text, root))
    graph = builder.build()
    return {(u, v) for u, v, d in graph.edges(data=True) if d.get("edge_type") == "calls"}


ONE_LINE = {
    "java": (
        "A.java",
        "class A {\n"
        "  void f(Object a) { f(a, 1); f(a, 1, 2); }\n"
        "  void f(Object a, int b) {}\n"
        "  void f(Object a, int b, int c) {}\n"
        "}\n",
    ),
    "csharp": (
        "A.cs",
        "class A {\n"
        "  void f(object a) { f(a, 1); f(a, 1, 2); }\n"
        "  void f(object a, int b) {}\n"
        "  void f(object a, int b, int c) {}\n"
        "}\n",
    ),
    "cpp": (
        "a.cpp",
        "class A {\n"
        " public:\n"
        "  void f(int a) { f(a, 1); f(a, 1, 2); }\n"
        "  void f(int a, int b) {}\n"
        "  void f(int a, int b, int c) {}\n"
        "};\n",
    ),
    "kotlin": (
        "A.kt",
        "class A {\n"
        "  fun f(a: Any) { f(a, 1); f(a, 1, 2) }\n"
        "  fun f(a: Any, b: Int) {}\n"
        "  fun f(a: Any, b: Int, c: Int) {}\n"
        "}\n",
    ),
}


@pytest.mark.parametrize("language", sorted(ONE_LINE))
def test_two_overloads_called_on_one_line_each_get_an_edge(language: str, tmp_path: Path) -> None:
    rel, text = ONE_LINE[language]
    caller = f"{rel}::A::f#1"
    assert _call_edges(tmp_path, rel, language, text) == {
        (caller, f"{rel}::A::f#2"),
        (caller, f"{rel}::A::f#3"),
    }


NESTED = {
    "java": (
        "A.java",
        "class A {\n"
        "  Object f(Object a) { return a; }\n"
        "  Object f(Object a, Object b) { return a; }\n"
        "  void g(Object x) { f(x, f(x)); }\n"
        "}\n",
    ),
    "csharp": (
        "A.cs",
        "class A {\n"
        "  object f(object a) { return a; }\n"
        "  object f(object a, object b) { return a; }\n"
        "  void g(object x) { f(x, f(x)); }\n"
        "}\n",
    ),
}


@pytest.mark.parametrize("language", sorted(NESTED))
def test_a_nested_call_to_another_overload_gets_its_own_edge(language: str, tmp_path: Path) -> None:
    rel, text = NESTED[language]
    caller = f"{rel}::A::g"
    edges = _call_edges(tmp_path, rel, language, text)
    assert {(caller, f"{rel}::A::f#1"), (caller, f"{rel}::A::f#2")} <= edges


def test_each_link_of_a_multi_line_chain_binds_its_own_overload(tmp_path: Path) -> None:
    """Both ``tag`` links report the chain's first line, ``head()``."""
    text = (
        "class B {\n"
        "  B head() { return this; }\n"
        "  B tag(int a) { return this; }\n"
        "  B tag(int a, int b) { return this; }\n"
        "  void run() {\n"
        "    head()\n"
        "        .tag(1)\n"
        "        .tag(1, 2);\n"
        "  }\n"
        "}\n"
    )
    edges = _call_edges(tmp_path, "B.java", "java", text)
    assert {("B.java::B::run", "B.java::B::tag#1"), ("B.java::B::run", "B.java::B::tag#2")} <= edges


def test_one_call_matched_by_two_patterns_stays_one_record() -> None:
    """``S.super.m()`` and C++ ``ns::f()`` are each matched twice on one node."""
    java = _parse("A.java", "java", "class A implements I { void m() { I.super.m(); } }\n")
    cpp = _parse("a.cpp", "cpp", "void g() { ns::f(1); }\n")
    assert [(c.target_name, c.scope_name) for c in java.calls] == [("m", "I")]
    assert [(c.target_name, c.scope_name) for c in cpp.calls] == [("f", "ns")]
