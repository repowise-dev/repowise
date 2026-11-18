"""The symbol id builder spells every id the graph builder emits."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.symbol_identity import (
    file_of_symbol,
    local_symbol_id,
    make_symbol_id,
    module_symbol_id,
)
from repowise.core.ingestion.traverser import FileInfo

# Nested classes, a SQL name qualified with a dot, and a path holding ``::``.
# Paths are only strings here, so the last one runs on every platform even
# though Windows cannot create such a file.
_FILES = {
    "pkg/mod.py": (
        "python",
        "class Outer:\n    class Inner:\n        def meth(self):\n            pass\n"
        "    def top(self):\n        pass\n\ndef f():\n    pass\n\nf()\n",
    ),
    "src/Foo.java": ("java", "package x;\npublic class Foo {\n  static class Bar {\n    void baz() {}\n  }\n}\n"),
    "db/schema.sql": ("sql", "CREATE TABLE app.users (id int);\n"),
    "a::b/odd.py": ("python", "def g():\n    pass\n"),
}


def _graph(root: Path):
    parser = ASTParser()
    builder = GraphBuilder(root)
    for path, (language, text) in _FILES.items():
        source = text.encode()
        info = FileInfo(
            path=path,
            abs_path=str(root / path),
            language=language,
            size_bytes=len(source),
            git_hash="0",
            last_modified=0.0,
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        builder.add_file(parser.parse_file(info, source))
    return builder.build()


def test_builder_reproduces_every_graph_symbol_id(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    expected = {
        make_symbol_id("pkg/mod.py", "Outer"),
        make_symbol_id("pkg/mod.py", "Outer", "Inner"),
        make_symbol_id("pkg/mod.py", "Inner", "meth"),
        make_symbol_id("pkg/mod.py", "Outer", "top"),
        make_symbol_id("pkg/mod.py", "f"),
        make_symbol_id("src/Foo.java", "Foo"),
        make_symbol_id("src/Foo.java", "Foo", "Bar"),
        make_symbol_id("src/Foo.java", "Bar", "baz"),
        make_symbol_id("db/schema.sql", "app.users"),
        make_symbol_id("a::b/odd.py", "g"),
        *(module_symbol_id(path) for path in _FILES),
    }
    symbols = {n for n, d in graph.nodes(data=True) if d.get("node_type") == "symbol"}
    assert symbols == expected
    # Spelled out once, so a change to the builder cannot pass by changing both sides.
    assert "pkg/mod.py::Outer::Inner" in symbols
    assert "pkg/mod.py::__module__" in symbols
    assert "db/schema.sql::app.users" in symbols


def test_file_of_symbol_reads_the_path_back(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    for node, data in graph.nodes(data=True):
        if data.get("node_type") == "symbol" and "::" not in data["file_path"]:
            assert file_of_symbol(node) == data["file_path"]


def test_helpers() -> None:
    assert make_symbol_id("a.py", "C", "m#1") == "a.py::C::m#1"
    assert module_symbol_id("a.py") == "a.py::__module__"
    assert file_of_symbol("a.py::C::m") == "a.py"
    assert file_of_symbol("a.py") == "a.py"
    assert local_symbol_id("a.py::C::m#1") == "C::m#1"
    assert local_symbol_id("a.py") == ""
