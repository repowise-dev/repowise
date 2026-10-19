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
    "lazy/pyproject.toml": '[project]\nname = "lazy"\n',
    "lazy/lazy/__init__.py": (
        "__all__ = ['extract', 'missing']\n"
        "_LAZY: dict[str, tuple[str, str]] = {\n"
        "    'extract': ('.extract', 'extract'),\n"
        "    'missing': ('.missing', 'missing'),\n"
        "}\n\n"
        "def __getattr__(name):\n"
        "    if name in _LAZY:\n"
        "        module_name, attr = _LAZY[name]\n"
        "        return getattr(importlib.import_module(module_name, __name__), attr)\n"
        "    raise AttributeError(name)\n"
    ),
    "lazy/lazy/extract.py": "def extract():\n    pass\n",
    "local/pyproject.toml": '[project]\nname = "local"\n',
    "local/local/__init__.py": (
        "def __getattr__(name):\n"
        "    lazy = {'extract': ('local.extract', 'extract')}\n"
        "    if name in lazy:\n"
        "        module_name, attr = lazy[name]\n"
        "        return getattr(importlib.import_module(module_name, __name__), attr)\n"
        "    raise AttributeError(name)\n"
    ),
    "local/local/extract.py": "def extract():\n    pass\n",
    "computed/pyproject.toml": '[project]\nname = "computed"\n',
    "computed/computed/__init__.py": (
        "__all__ = ['extract']\n"
        "_NAMES = ['extract']\n"
        "_LAZY = {name: ('.extract', name) for name in _NAMES}\n\n"
        "def __getattr__(name):\n"
        "    if name in _LAZY:\n"
        "        module_name, attr = _LAZY[name]\n"
        "        return getattr(importlib.import_module(module_name, __name__), attr)\n"
        "    raise AttributeError(name)\n"
    ),
    "computed/computed/extract.py": "def extract():\n    pass\n",
    "shadow/pyproject.toml": '[project]\nname = "shadow"\n',
    "shadow/shadow/__init__.py": (
        "__all__ = ['Client']\n"
        "from .eager import Client\n"
        "_LAZY = {'Client': ('.lazy', 'Client')}\n\n"
        "def __getattr__(name):\n"
        "    if name in _LAZY:\n"
        "        module_name, attr = _LAZY[name]\n"
        "        return getattr(importlib.import_module(module_name, __name__), attr)\n"
        "    raise AttributeError(name)\n"
    ),
    "shadow/shadow/eager.py": "class Client:\n    pass\n",
    "shadow/shadow/lazy.py": "class Client:\n    pass\n",
    "rebind/pyproject.toml": '[project]\nname = "rebind"\n',
    "rebind/rebind/__init__.py": (
        "__all__ = ['extract']\n"
        "_LAZY = {'extract': '.first'}\n"
        "_LAZY = {'extract': '.second'}\n\n"
        "def __getattr__(name):\n"
        "    if name in _LAZY:\n"
        "        return getattr(importlib.import_module(_LAZY[name], __name__), name)\n"
        "    raise AttributeError(name)\n"
    ),
    "rebind/rebind/first.py": "def extract():\n    pass\n",
    "rebind/rebind/second.py": "def extract():\n    pass\n",
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


def build_fixture(repo: Path, files: dict[str, str] = _REPO) -> GraphBuilder:
    for rel, body in files.items():
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
    "lazy/lazy/extract.py::extract": "lazy/lazy/__init__.py",
    "local/local/extract.py::extract": "local/local/__init__.py",
    "shadow/shadow/eager.py::Client": "shadow/shadow/__init__.py",
    "rebind/rebind/second.py::extract": "rebind/rebind/__init__.py",
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


def test_maven_published_module_flags_public_api(tmp_path: Path) -> None:
    files = {
        "pom.xml": (
            '<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
            '  <modelVersion>4.0.0</modelVersion>\n'
            '  <groupId>com.example</groupId>\n'
            '  <artifactId>parent</artifactId>\n'
            '  <version>1.0.0</version>\n'
            '  <packaging>pom</packaging>\n'
            '  <modules><module>lib</module></modules>\n'
            '</project>\n'
        ),
        "lib/pom.xml": (
            '<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
            '  <modelVersion>4.0.0</modelVersion>\n'
            '  <groupId>com.example</groupId>\n'
            '  <artifactId>lib</artifactId>\n'
            '  <version>1.0.0</version>\n'
            '  <packaging>jar</packaging>\n'
            '</project>\n'
        ),
        "lib/src/main/java/a/Api.java": (
            "package a;\n\n"
            "public class Api {\n"
            "    public void run() {}\n"
            "    private void secret() {}\n"
            "}\n"
        ),
        "lib/src/main/java/a/Hidden.java": (
            "package a;\n\n"
            "class Hidden {\n"
            "    public void foo() {}\n"
            "}\n"
        ),
        "lib/src/test/java/a/ApiTest.java": (
            "package a;\n\n"
            "public class ApiTest {\n"
            "    public void testRun() {}\n"
            "}\n"
        ),
    }
    graph = build_fixture(tmp_path, files).graph()
    flags = {
        node: data["public_api"]
        for node, data in graph.nodes(data=True)
        if data.get("node_type") == "symbol" and "public_api" in data
    }
    assert flags["lib/src/main/java/a/Api.java::Api"] == "lib/src/main/java/a/Api.java"
    assert flags["lib/src/main/java/a/Api.java::Api::run"] == "lib/src/main/java/a/Api.java"
    assert flags.get("lib/src/main/java/a/Api.java::Api::secret", "") == ""
    assert flags.get("lib/src/main/java/a/Hidden.java::Hidden", "") == ""
    assert flags.get("lib/src/test/java/a/ApiTest.java::ApiTest", "") == ""


def test_gradle_and_jpms_published_module_flags_public_api(tmp_path: Path) -> None:
    files = {
        "settings.gradle.kts": 'include("lib")\ninclude("app")\n',
        "lib/build.gradle.kts": 'plugins {\n    `java-library`\n}\n',
        "app/build.gradle.kts": 'plugins {\n    application\n}\n',
        "lib/src/main/java/module-info.java": (
            "module lib {\n"
            "    exports a;\n"
            "}\n"
        ),
        "lib/src/main/java/a/LibApi.java": (
            "package a;\n\n"
            "public class LibApi {\n"
            "    public void serve() {}\n"
            "}\n"
        ),
        "lib/src/main/java/a/impl/InternalLib.java": (
            "package a.impl;\n\n"
            "public class InternalLib {\n"
            "    public void internalServe() {}\n"
            "}\n"
        ),
        "app/src/main/java/b/AppMain.java": (
            "package b;\n\n"
            "public class AppMain {\n"
            "    public void start() {}\n"
            "}\n"
        ),
    }
    graph = build_fixture(tmp_path, files).graph()
    flags = {
        node: data["public_api"]
        for node, data in graph.nodes(data=True)
        if data.get("node_type") == "symbol" and "public_api" in data
    }
    assert flags["lib/src/main/java/a/LibApi.java::LibApi"] == "lib/src/main/java/a/LibApi.java"
    assert flags["lib/src/main/java/a/LibApi.java::LibApi::serve"] == "lib/src/main/java/a/LibApi.java"
    assert flags.get("lib/src/main/java/a/impl/InternalLib.java::InternalLib", "") == ""
    assert flags.get("lib/src/main/java/a/impl/InternalLib.java::InternalLib::internalServe", "") == ""
    assert flags.get("app/src/main/java/b/AppMain.java::AppMain", "") == ""


def test_kotlin_public_api_and_visibility(tmp_path: Path) -> None:
    files = {
        "build.gradle.kts": 'plugins {\n    `java-library`\n}\n',
        "src/main/kotlin/a/Open.kt": (
            "package a\n\n"
            "class Open {\n"
            "    fun execute() {}\n"
            "}\n"
        ),
        "src/main/kotlin/a/Hidden.kt": (
            "package a\n\n"
            "internal class Hidden {\n"
            "    fun run() {}\n"
            "}\n\n"
            "internal object HiddenObj {\n"
            "    fun compute() {}\n"
            "}\n"
        ),
    }
    parser = ASTParser()
    hidden_bytes = (
        b"package a\n\n"
        b"internal class Hidden {\n"
        b"    fun run() {}\n"
        b"}\n\n"
        b"internal object HiddenObj {\n"
        b"    fun compute() {}\n"
        b"}\n"
    )
    from repowise.core.ingestion.traverser import FileInfo
    fi = FileInfo(
        path="src/main/kotlin/a/Hidden.kt",
        abs_path=str(tmp_path / "src/main/kotlin/a/Hidden.kt"),
        language="kotlin",
        size_bytes=len(hidden_bytes),
        git_hash="0",
        last_modified=0.0,
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    pf = parser.parse_file(fi, hidden_bytes)
    symbols = {s.name: s.visibility for s in pf.symbols}
    assert symbols["Hidden"] == "internal"
    assert symbols["HiddenObj"] == "internal"

    graph = build_fixture(tmp_path, files).graph()
    flags = {
        node: data["public_api"]
        for node, data in graph.nodes(data=True)
        if data.get("node_type") == "symbol" and "public_api" in data
    }
    assert flags["src/main/kotlin/a/Open.kt::Open"] == "src/main/kotlin/a/Open.kt"
    assert flags["src/main/kotlin/a/Open.kt::Open::execute"] == "src/main/kotlin/a/Open.kt"
    assert flags.get("src/main/kotlin/a/Hidden.kt::Hidden", "") == ""
    assert flags.get("src/main/kotlin/a/Hidden.kt::HiddenObj", "") == ""


_GO_REPO = {
    "go.mod": "module example.com/lib\n\ngo 1.21\n",
    "lib/client.go": (
        "package lib\n\ntype Client struct{}\n\nfunc New() *Client { return &Client{} }\n\n"
        "func helper() {}\n"
    ),
    "lib/send.go": (
        "package lib\n\nfunc (c *Client) Send() error { return nil }\n\n"
        "func (c *Client) retry() {}\n"
    ),
    "lib/internal/x/x.go": "package x\n\nfunc Hidden() {}\n",
    "lib/x_test.go": "package lib\n\nfunc TestHelper() {}\n",
    "cmd/tool/main.go": (
        'package main\n\nimport "example.com/lib/lib"\n\nfunc Run() { _ = lib.New() }\n\n'
        "func main() { Run() }\n"
    ),
}


def _public_flags(graph) -> dict[str, str]:
    return {
        node: data["public_api"]
        for node, data in graph.nodes(data=True)
        if data.get("node_type") == "symbol" and "public_api" in data
    }


def test_go_exported_identifiers_of_importable_packages_are_public(tmp_path: Path) -> None:
    flags = _public_flags(build_fixture(tmp_path, _GO_REPO).graph())
    assert {node: root for node, root in flags.items() if root} == {
        "lib/client.go::Client": "lib/client.go",
        "lib/client.go::New": "lib/client.go",
        # A method declared apart from its receiver type is exported with it.
        "lib/send.go::Client::Send": "lib/send.go",
    }
    # Unexported names, an ``internal`` package, a ``package main``, a test
    # file's exported func and an unexported method export nothing.
    for node in (
        "lib/client.go::helper",
        "lib/send.go::Client::retry",
        "lib/internal/x/x.go::Hidden",
        "cmd/tool/main.go::Run",
        "cmd/tool/main.go::main",
        "lib/x_test.go::TestHelper",
    ):
        assert flags.get(node, "") == "", node


def test_go_without_a_go_mod_exports_nothing(tmp_path: Path) -> None:
    files = {rel: body for rel, body in _GO_REPO.items() if rel != "go.mod"}
    flags = _public_flags(build_fixture(tmp_path, files).graph())
    assert flags
    assert not {node for node, root in flags.items() if root}


def test_go_method_of_an_unexported_type_is_not_public(tmp_path: Path) -> None:
    files = {
        "go.mod": "module example.com/lib\n",
        "lib/a.go": "package lib\n\ntype client struct{}\n",
        "lib/b.go": "package lib\n\nfunc (c *client) Send() {}\n",
    }
    flags = _public_flags(build_fixture(tmp_path, files).graph())
    assert flags["lib/b.go::client::Send"] == ""