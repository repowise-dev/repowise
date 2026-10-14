"""A module that declares a program ``main`` is run, never imported.

A Gradle or Maven module whose Kotlin ``fun main`` or Java launcher ``main``
starts the application, or a Flutter package with ``lib/main.dart``, has no
importer by design, so it is not a zombie package, and the class holding a
JVM ``main`` is not an unused export.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.jvm_reachability import defines_main
from repowise.core.ingestion.graph.builder import GraphBuilder
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.traverser import FileTraverser

_FILES = {
    "settings.gradle.kts": 'include("server", "tool", "worker", "shared")\n',
    # Kotlin application module.
    "server/build.gradle.kts": "plugins { application }\n",
    "server/src/main/kotlin/demo/Launcher.kt": (
        "package demo\n\nfun main(args: Array<String>) {\n    println(args.size)\n}\n"
    ),
    # Java application module: the launcher class has no importer.
    "tool/pom.xml": "<project/>\n",
    "tool/src/main/java/demo/tool/Tool.java": (
        "package demo.tool;\n\npublic class Tool {\n"
        "    public static void main(String[] args) {}\n}\n"
    ),
    # Java 25 launches an instance ``void main()``.
    "applet/pom.xml": "<project/>\n",
    "applet/src/main/java/demo/applet/Hello.java": (
        "package demo.applet;\n\npublic class Hello {\n    void main() {}\n}\n"
    ),
    # A ``main(int)`` is an ordinary method.
    "worker/pom.xml": "<project/>\n",
    "worker/src/main/java/demo/worker/Worker.java": (
        "package demo.worker;\n\npublic class Worker {\n    public void main(int n) {}\n}\n"
    ),
    # A library module nobody imports.
    "shared/build.gradle.kts": "plugins { `java-library` }\n",
    "shared/src/main/kotlin/demo/shared/Strings.kt": (
        "package demo.shared\n\nfun shout(s: String): String = s.uppercase()\n"
    ),
    # Flutter app.
    "mobile/pubspec.yaml": "name: mobile\n",
    "mobile/lib/main.dart": "void main() {}\n",
    # A Rust library: ``lib.rs`` is a root, but not a program.
    "old-rs/Cargo.toml": '[package]\nname = "old"\n',
    "old-rs/src/lib.rs": "pub fn h() {}\n",
}


def _report(root: Path):
    for rel, text in _FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    parser = ASTParser()
    builder = GraphBuilder(repo_path=root)
    source_map: dict[str, bytes] = {}
    for fi in FileTraverser(root).traverse():
        source = Path(fi.abs_path).read_bytes()
        source_map[fi.path] = source
        builder.add_file(parser.parse_file(fi, source))
    builder.set_source_map(source_map)
    analyzer = DeadCodeAnalyzer(
        builder.build(), git_meta_map={}, source_map=source_map, repo_root=root
    )
    return analyzer.analyze({"min_confidence": 0.0})


def _of(report, kind: DeadCodeKind) -> set[str]:
    return {f.symbol_name or f.file_path for f in report.findings if f.kind is kind}


def test_program_modules_are_not_zombies_and_libraries_still_are(tmp_path: Path):
    zombies = _of(_report(tmp_path), DeadCodeKind.ZOMBIE_PACKAGE)
    assert zombies.isdisjoint({"server", "tool", "applet", "mobile"})
    assert {"shared", "worker", "old-rs"} <= zombies


def test_java_main_class_is_not_an_unused_export(tmp_path: Path):
    exports = _of(_report(tmp_path), DeadCodeKind.UNUSED_EXPORT)
    assert "Tool" not in exports
    assert "Worker" in exports


def _graph(path: str, *symbols: dict) -> nx.DiGraph:
    g = nx.DiGraph()
    g.add_node(path, node_type="file")
    for i, sym in enumerate(symbols):
        g.add_node(f"{path}::{i}", node_type="symbol", **sym)
        g.add_edge(path, f"{path}::{i}", edge_type="defines")
    return g


def test_defines_main_reads_the_name_and_the_launcher_shape():
    def java(sig: str) -> bool:
        sym = {"kind": "method", "name": "main", "parent_name": "A", "signature": sig}
        return defines_main(_graph("A.java", sym), "A.java")

    assert java("main(String[] args) -> void")
    assert java("main(String... args) -> void")
    assert java("main(String args[]) -> void")
    assert java("main(final java.lang.String[] argv) -> void")
    assert java("main(@Nullable String[] args) -> void")
    assert java("main(final @Foo(x = 1) String[] a) -> void")
    assert java("main(String @Foo [] a) -> void")
    assert java("main() -> void")
    assert not java("main(String s) -> void")
    assert not java("main(int x) -> void")
    # The id of an overloaded Java method carries a discriminator, the name does not.
    assert not defines_main(
        _graph("B.java", {"kind": "method", "name": "main#1(String[])", "signature": "x"}),
        "B.java",
    )

    def kotlin(**sym) -> bool:
        return defines_main(_graph("K.kt", {"name": "main", **sym}), "K.kt")

    assert kotlin(kind="function", signature="function main()")
    assert kotlin(kind="method", parent_name="Obj", decorators=["@JvmStatic"])
    assert not kotlin(kind="method", parent_name="K", decorators=[])
    # Only JVM files.
    assert not defines_main(_graph("m.py", {"kind": "function", "name": "main"}), "m.py")
