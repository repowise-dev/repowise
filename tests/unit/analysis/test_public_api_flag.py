"""Which symbols a package exports, stamped on the graph per symbol."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.traverser import FileTraverser

_REPO = {
    # A library another package here imports, and an application that runs a
    # console script.
    "lib/pyproject.toml": '[project]\nname = "lib"\n',
    "lib/lib/__init__.py": "from .core import Client, internal\n__all__ = ['Client']\n",
    "lib/lib/core.py": (
        "from .hidden import helper\n\n"
        "class Client:\n    def send(self):\n        pass\n\n    def _retry(self):\n        pass\n\n"
        "def internal():\n    pass\n\ndef unlisted():\n    pass\n"
    ),
    "lib/lib/store/__init__.py": "from .disk import Store\n",
    "lib/lib/store/disk.py": "class Store:\n    pass\n",
    "lib/lib/hidden/__init__.py": "from .impl import helper\n",
    "lib/lib/hidden/impl.py": "def helper():\n    pass\n",
    "app/pyproject.toml": (
        '[project]\nname = "app"\n[project.scripts]\napp = "app.main:run"\n'
    ),
    "app/app/__init__.py": "",
    "app/app/main.py": (
        "from lib.store import Store\nfrom app.schemas import Model\n\ndef run():\n    pass\n"
    ),
    "app/app/schemas/__init__.py": "from .models import Model\n",
    "app/app/schemas/models.py": "class Model:\n    pass\n",
    "tests/__init__.py": "from .helpers import fixture\n",
    "tests/helpers.py": "def fixture():\n    pass\n",
    "package.json": json.dumps({"private": True, "workspaces": ["web"]}),
    "web/package.json": json.dumps(
        {"name": "web", "exports": {".": "./src/main.ts", "./extra": "./src/extra.ts"}}
    ),
    "web/src/main.ts": "export { render } from './render';\n",
    "web/src/render.ts": "export function render(): void {}\nexport function hidden(): void {}\n",
    "web/src/extra.ts": "export function extra(): void {}\n",
}


def build_fixture(repo: Path) -> GraphBuilder:
    for rel, body in _REPO.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body, encoding="utf-8")
    parser = ASTParser()
    builder = GraphBuilder(repo)
    for fi in FileTraverser(repo).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    builder.build()
    return builder


EXPORTED = {
    "lib/lib/core.py::Client": "lib/lib/__init__.py",
    "lib/lib/core.py::Client::send": "lib/lib/__init__.py",
    "lib/lib/store/disk.py::Store": "lib/lib/store/__init__.py",
    "web/src/render.ts::render": "web/src/main.ts",
    "web/src/extra.ts::extra": "web/src/extra.ts",
}


def test_exported_symbols_carry_the_file_that_exports_them(tmp_path: Path) -> None:
    graph = build_fixture(tmp_path).graph()
    flags = {
        node: data["public_api"]
        for node, data in graph.nodes(data=True)
        if data.get("node_type") == "symbol" and "public_api" in data
    }
    assert {node: root for node, root in flags.items() if root} == EXPORTED
    # ``__all__`` leaves ``internal`` out; a private method, a console script's
    # module, a test barrel, a library barrel only its own package reads and
    # an application's subpackage barrel export nothing.
    for node in (
        "lib/lib/core.py::internal",
        "lib/lib/core.py::unlisted",
        "lib/lib/core.py::Client::_retry",
        "lib/lib/hidden/impl.py::helper",
        "app/app/main.py::run",
        "app/app/schemas/models.py::Model",
        "tests/helpers.py::fixture",
        "web/src/render.ts::hidden",
    ):
        assert flags[node] == "", node

