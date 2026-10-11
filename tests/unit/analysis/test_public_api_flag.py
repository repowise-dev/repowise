"""Which symbols a package exports, stamped on the graph per symbol."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.traverser import FileTraverser

_REPO = {
    "pyproject.toml": (
        '[project]\nname = "pkg"\n[project.scripts]\npkg = "pkg.cli:main"\n'
    ),
    "pkg/__init__.py": "from .core import Client, internal\n__all__ = ['Client']\n",
    "pkg/core.py": (
        "class Client:\n    def send(self):\n        pass\n\n    def _retry(self):\n        pass\n\n"
        "def internal():\n    pass\n\ndef unlisted():\n    pass\n"
    ),
    "pkg/cli.py": "def main():\n    pass\n",
    "tests/__init__.py": "from .helpers import fixture\n",
    "tests/helpers.py": "def fixture():\n    pass\n",
    "web/package.json": json.dumps({"name": "web", "main": "./src/main.ts"}),
    "web/src/main.ts": "export { render } from './render';\n",
    "web/src/render.ts": "export function render(): void {}\nexport function hidden(): void {}\n",
}


def _graph(repo: Path):
    for rel, body in _REPO.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body, encoding="utf-8")
    parser = ASTParser()
    builder = GraphBuilder(repo)
    for fi in FileTraverser(repo).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


def test_exported_symbols_carry_the_file_that_exports_them(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    flags = {
        node: data["public_api"]
        for node, data in graph.nodes(data=True)
        if data.get("node_type") == "symbol" and "public_api" in data
    }
    exported = {node: root for node, root in flags.items() if root}
    assert exported == {
        "pkg/core.py::Client": "pkg/__init__.py",
        "pkg/core.py::Client::send": "pkg/__init__.py",
        "web/src/render.ts::render": "web/src/main.ts",
    }
    # ``__all__`` leaves ``internal`` out; a private method, a console script's
    # module and a test package's barrel export nothing.
    for node in (
        "pkg/core.py::internal",
        "pkg/core.py::unlisted",
        "pkg/core.py::Client::_retry",
        "pkg/cli.py::main",
        "tests/helpers.py::fixture",
        "web/src/render.ts::hidden",
    ):
        assert flags[node] == "", node
