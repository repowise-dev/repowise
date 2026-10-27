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

# ---------------------------------------------------------------------------
# C#: a type of a project that ships a package is public API
# ---------------------------------------------------------------------------


def _csproj(props: str = "") -> str:
    return f'<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>{props}</PropertyGroup></Project>'


def build_files(repo: Path, files: dict[str, str]) -> dict[str, str]:
    """Build a graph from *files*; ``{symbol id: public_api}`` for every symbol."""
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body, encoding="utf-8")
    parser = ASTParser()
    builder = GraphBuilder(repo)
    for fi in FileTraverser(repo).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    builder.build()
    return {
        node: data["public_api"]
        for node, data in builder.graph().nodes(data=True)
        if data.get("node_type") == "symbol" and "public_api" in data
    }


_CSHARP_REPO = {
    # Packs by opting in.
    "src/Lib/Lib.csproj": _csproj("<IsPackable>true</IsPackable>"),
    "src/Lib/Client.cs": (
        "namespace Lib;\n"
        "public class Client\n{\n    public void Send() { }\n    private void Retry() { }\n}\n"
        "internal class Helper\n{\n    public void Go() { }\n}\n"
        "class Implicit\n{\n    public void Go() { }\n}\n"
    ),
    # Sets no packing property: shipped by the baseline it keeps.
    "src/Base/Base.csproj": _csproj(),
    "src/Base/.PublicAPI/PublicAPI.Shipped.txt": "Base.Thing\n",
    "src/Base/Thing.cs": "namespace Base;\npublic class Thing\n{\n    public void Do() { }\n}\n",
    # Says nothing about packing, so ships nothing.
    "samples/App/App.csproj": _csproj(),
    "samples/App/Program.cs": "namespace App;\npublic class Program\n{\n    public void Main() { }\n}\n",
    "tests/Lib.Tests/Lib.Tests.csproj": _csproj(),
    "tests/Lib.Tests/ClientTests.cs": (
        "namespace Lib.Tests;\npublic class ClientTests\n{\n    public void Works() { }\n}\n"
    ),
    # An explicit no beats a PackageId.
    "src/Nope/Nope.csproj": _csproj("<IsPackable>false</IsPackable><PackageId>Nope</PackageId>"),
    "src/Nope/Widget.cs": "namespace Nope;\npublic class Widget\n{\n    public void Spin() { }\n}\n",
    # A sample nested in the packaged library's folder is not part of the package.
    "src/Lib/samples/Demo/Demo.csproj": _csproj(),
    "src/Lib/samples/Demo/Demo.cs": "namespace Demo;\npublic class Demo\n{\n    public void Run() { }\n}\n",
}


def test_public_csharp_types_of_a_shipped_project_are_public_api(tmp_path: Path) -> None:
    flags = build_files(tmp_path, _CSHARP_REPO)
    stamped = {node for node, root in flags.items() if root}
    assert stamped == {
        "src/Lib/Client.cs::Client",
        "src/Lib/Client.cs::Client::Send",
        "src/Base/Thing.cs::Thing",
        "src/Base/Thing.cs::Thing::Do",
    }
    # The file that exports a symbol is the file that declares it.
    assert flags["src/Lib/Client.cs::Client::Send"] == "src/Lib/Client.cs"
    assert flags["src/Base/Thing.cs::Thing"] == "src/Base/Thing.cs"
    # A private member, internal and modifier-less types (and their public
    # members), and every type of a project that ships nothing stay unflagged.
    for node in (
        "src/Lib/Client.cs::Client::Retry",
        "src/Lib/Client.cs::Helper",
        "src/Lib/Client.cs::Helper::Go",
        "src/Lib/Client.cs::Implicit",
        "src/Lib/Client.cs::Implicit::Go",
        "samples/App/Program.cs::Program",
        "samples/App/Program.cs::Program::Main",
        "tests/Lib.Tests/ClientTests.cs::ClientTests",
        "tests/Lib.Tests/ClientTests.cs::ClientTests::Works",
        "src/Nope/Widget.cs::Widget",
        "src/Nope/Widget.cs::Widget::Spin",
        "src/Lib/samples/Demo/Demo.cs::Demo",
        "src/Lib/samples/Demo/Demo.cs::Demo::Run",
    ):
        assert flags[node] == "", node


def test_a_repo_with_no_readable_project_stamps_no_csharp(tmp_path: Path) -> None:
    files = {rel: body for rel, body in _CSHARP_REPO.items() if not rel.endswith(".csproj")}
    assert not {node for node, root in build_files(tmp_path, files).items() if root}


def test_every_generic_arity_of_a_public_type_is_public_api(tmp_path: Path) -> None:
    flags = build_files(
        tmp_path,
        {
            "Lib/Lib.csproj": _csproj("<IsPackable>true</IsPackable>"),
            "Lib/Box.cs": (
                "namespace Lib;\n"
                "public class Box<T>\n{\n    public void Put() { }\n}\n"
                "public class Box\n{\n    public void Open() { }\n}\n"
            ),
        },
    )
    # ``Box<T>`` and ``Box`` share a name; the id tells them apart.
    assert {node for node, root in flags.items() if root} == {
        "Lib/Box.cs::Box`1",
        "Lib/Box.cs::Box`1::Put",
        "Lib/Box.cs::Box",
        "Lib/Box.cs::Box::Open",
    }


def test_a_partial_fragment_without_a_modifier_takes_the_public_part(tmp_path: Path) -> None:
    flags = build_files(
        tmp_path,
        {
            "src/Lib/Lib.csproj": _csproj("<IsPackable>true</IsPackable>"),
            "src/Lib/Pipeline.cs": (
                "namespace Lib;\npublic partial class Pipeline\n{\n    public void Build() { }\n}\n"
            ),
            "src/Lib/Pipeline.Sync.cs": (
                "namespace Lib;\npartial class Pipeline\n{\n"
                "    public void Run() { }\n    private void Spin() { }\n}\n"
            ),
        },
    )
    assert flags["src/Lib/Pipeline.cs::Pipeline::Build"] == "src/Lib/Pipeline.cs"
    # The part with no modifier is the public type too, and so are its members.
    assert flags["src/Lib/Pipeline.Sync.cs::Pipeline"] == "src/Lib/Pipeline.Sync.cs"
    assert flags["src/Lib/Pipeline.Sync.cs::Pipeline::Run"] == "src/Lib/Pipeline.Sync.cs"
    assert flags["src/Lib/Pipeline.Sync.cs::Pipeline::Spin"] == ""


def test_partial_fragments_of_an_internal_type_stay_unflagged(tmp_path: Path) -> None:
    flags = build_files(
        tmp_path,
        {
            "Lib/Lib.csproj": _csproj("<IsPackable>true</IsPackable>"),
            "Lib/Engine.cs": "namespace Lib;\ninternal partial class Engine\n{\n    public void A() { }\n}\n",
            "Lib/Engine.More.cs": "namespace Lib;\npartial class Engine\n{\n    public void B() { }\n}\n",
        },
    )
    assert not {node for node, root in flags.items() if root}


def test_partial_fragments_in_a_project_that_ships_nothing_stay_unflagged(tmp_path: Path) -> None:
    flags = build_files(
        tmp_path,
        {
            "App/App.csproj": _csproj(),
            "App/Job.cs": "namespace App;\npublic partial class Job\n{\n    public void A() { }\n}\n",
            "App/Job.More.cs": "namespace App;\npartial class Job\n{\n    public void B() { }\n}\n",
        },
    )
    assert not {node for node, root in flags.items() if root}


_GEMSPEC = "Gem::Specification.new do |spec|\n  spec.name = '{name}'\n  spec.version = '0.1'\nend\n"

_RUBY_GEM = {
    "mygem.gemspec": _GEMSPEC.format(name="mygem"),
    "lib/mygem.rb": (
        "require 'mygem/client'\n\n"
        "module Mygem\n  def self.new(url)\n    Client.new(url)\n  end\nend\n"
    ),
    "lib/mygem/client.rb": (
        "module Mygem\n  class Client\n    def get(path)\n    end\n\n"
        "    private\n\n    def retry_once\n    end\n  end\nend\n"
    ),
    "lib/mygem/unloaded.rb": "module Mygem\n  class Unloaded\n    def run\n    end\n  end\nend\n",
    "spec/client_spec.rb": "require 'mygem'\n\ndescribe 'client' do\nend\n",
}


def _stamped(flags: dict[str, str]) -> set[str]:
    return {node for node, root in flags.items() if root}


def test_a_gems_entry_and_what_it_requires_are_public_api(tmp_path: Path) -> None:
    flags = build_files(tmp_path, _RUBY_GEM)
    assert _stamped(flags) == {
        "lib/mygem.rb::Mygem",
        "lib/mygem.rb::Mygem::new",
        "lib/mygem/client.rb::Mygem",
        "lib/mygem/client.rb::Mygem::Client",
        "lib/mygem/client.rb::Client::get",
    }
    # The file that exports a symbol is the file that declares it.
    assert flags["lib/mygem/client.rb::Client::get"] == "lib/mygem/client.rb"
    # A method below a bare ``private``, a file nothing requires and a spec stay out.
    for node in (
        "lib/mygem/client.rb::Client::retry_once",
        "lib/mygem/unloaded.rb::Mygem",
        "lib/mygem/unloaded.rb::Mygem::Unloaded",
        "lib/mygem/unloaded.rb::Unloaded::run",
    ):
        assert flags[node] == "", node
    assert not [node for node in flags if node.startswith("spec/") and flags[node]]


def test_a_dashed_gem_name_reads_as_path_separators(tmp_path: Path) -> None:
    flags = build_files(
        tmp_path,
        {
            "rack-protection.gemspec": _GEMSPEC.format(name="rack-protection"),
            "lib/rack/protection.rb": (
                "module Rack\n  module Protection\n    def self.new\n    end\n  end\nend\n"
            ),
        },
    )
    assert flags["lib/rack/protection.rb::Rack"] == "lib/rack/protection.rb"
    assert flags["lib/rack/protection.rb::Rack::Protection"] == "lib/rack/protection.rb"
    assert flags["lib/rack/protection.rb::Protection::new"] == "lib/rack/protection.rb"


def test_a_lib_without_a_gemspec_stamps_nothing(tmp_path: Path) -> None:
    files = {rel: body for rel, body in _RUBY_GEM.items() if not rel.endswith(".gemspec")}
    flags = build_files(tmp_path, files)
    assert flags and not _stamped(flags)


def test_a_gem_whose_entry_file_is_absent_stamps_nothing(tmp_path: Path) -> None:
    files = {rel: body for rel, body in _RUBY_GEM.items() if rel != "lib/mygem.rb"}
    assert not _stamped(build_files(tmp_path, files))


def test_the_gem_name_is_read_from_specification_new_or_the_file(tmp_path: Path) -> None:
    flags = build_files(
        tmp_path,
        {
            "one/one.gemspec": "Gem::Specification.new 'one', '1.0' do |s|\nend\n",
            "one/lib/one.rb": "module One\nend\n",
            # A name that is not a literal falls back to the gemspec's file name.
            "two/two.gemspec": "Gem::Specification.new do |s|\n  s.name = Two::NAME\nend\n",
            "two/lib/two.rb": "module Two\nend\n",
        },
    )
    assert flags["one/lib/one.rb::One"] == "one/lib/one.rb"
    assert flags["two/lib/two.rb::Two"] == "two/lib/two.rb"


def test_a_gems_requires_are_followed_transitively_inside_its_own_lib(tmp_path: Path) -> None:
    flags = build_files(
        tmp_path,
        {
            "a/a.gemspec": _GEMSPEC.format(name="a"),
            "a/lib/a.rb": "require_relative 'a/one'\nmodule A\nend\n",
            "a/lib/a/one.rb": "require 'a/two'\nmodule A\n  class One\n  end\nend\n",
            "a/lib/a/two.rb": "require 'b'\nmodule A\n  class Two\n  end\nend\n",
            # Another gem's lib, reached by a require, is not this gem's surface.
            "b/b.gemspec": _GEMSPEC.format(name="b-extra"),
            "b/lib/b.rb": "module B\n  class Helper\n  end\nend\n",
        },
    )
    assert flags["a/lib/a/one.rb::A::One"] == "a/lib/a/one.rb"
    assert flags["a/lib/a/two.rb::A::Two"] == "a/lib/a/two.rb"
    assert flags["b/lib/b.rb::B::Helper"] == ""


def test_members_of_deeply_nested_ruby_modules_are_public_api(tmp_path: Path) -> None:
    flags = build_files(
        tmp_path,
        {
            "deep.gemspec": _GEMSPEC.format(name="deep"),
            "lib/deep.rb": (
                "module Deep\n  module Http\n    class Conn\n      def run\n      end\n\n"
                "      class Inner\n        def go\n        end\n      end\n"
                "    end\n  end\nend\n"
            ),
        },
    )
    assert _stamped(flags) == {
        "lib/deep.rb::Deep",
        "lib/deep.rb::Deep::Http",
        "lib/deep.rb::Http::Conn",
        "lib/deep.rb::Conn::run",
        "lib/deep.rb::Conn::Inner",
        "lib/deep.rb::Inner::go",
    }


def test_ruby_methods_made_private_after_their_definition_are_not_public_api(
    tmp_path: Path,
) -> None:
    flags = build_files(
        tmp_path,
        {
            "vis.gemspec": _GEMSPEC.format(name="vis"),
            "lib/vis.rb": (
                "module Vis\n  class Box\n    def open\n    end\n\n    def hidden\n    end\n"
                "    private :hidden\n\n    protected\n\n    def guarded\n    end\n"
                "    public\n\n    def back\n    end\n\n"
                "    private def wrapped\n    end\n  end\nend\n"
            ),
        },
    )
    assert {n for n in _stamped(flags) if "::Box::" in n} == {
        "lib/vis.rb::Box::open",
        "lib/vis.rb::Box::back",
    }
