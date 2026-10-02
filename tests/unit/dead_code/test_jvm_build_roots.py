"""JVM classes a build or a harness loads, and types used by bare name.

Shapes from elasticsearch, every one reported dead: a plugin class named only
in ``esplugin { classname '...' }``, a JMH benchmark whose ``@State`` sits
behind another class annotation, and a type used from its own package or
further down its own file with no import.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_FILES = {
    "settings.gradle": "rootProject.name = 'sample'\n",
    "build.gradle": "apply plugin: 'java'\n",
    "gradle/plugins/src/main/kotlin/java-library.sample.gradle.kts": "plugins { java }\n",
    "qa/system-indices/build.gradle": (
        "esplugin {\n"
        "  name = 'system-indices-qa'\n"
        "  classname = 'org.sample.indices.SystemIndicesQA'\n"
        "}\n"
    ),
    "qa/system-indices/src/main/java/org/sample/indices/SystemIndicesQA.java": (
        "package org.sample.indices;\n\npublic class SystemIndicesQA {\n}\n"
    ),
    "benchmarks/src/main/java/org/sample/bench/SortBench.java": (
        "package org.sample.bench;\n\n"
        "@Fork(1)\n@State(Scope.Thread)\npublic class SortBench {\n"
        "    @Benchmark\n    public void sort() {}\n}\n"
    ),
    "server/src/main/java/org/sample/core/FilterType.java": (
        "package org.sample.core;\n\npublic enum FilterType { A, B }\n"
    ),
    "server/src/main/java/org/sample/core/FilterRef.java": (
        "package org.sample.core;\n\npublic class FilterRef {\n"
        "    public enum Mode { FAST, SLOW }\n\n"
        "    FilterType type;\n    Mode mode;\n"
        "    public static void main(String[] args) {}\n}\n"
    ),
    "server/src/main/java/org/sample/core/Orphan.java": (
        "package org.sample.core;\n\npublic class Orphan {\n}\n"
    ),
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
    graph = builder.build()
    analyzer = DeadCodeAnalyzer(graph, git_meta_map={}, source_map=source_map)
    return analyzer.analyze({"min_confidence": 0.0, "detect_zombie_packages": False})


def _named(report, kind: DeadCodeKind) -> set[str]:
    return {f.symbol_name or Path(f.file_path).name for f in report.findings if f.kind is kind}


def test_build_named_plugin_class_is_not_reported(tmp_path: Path):
    report = _report(tmp_path)
    assert "SystemIndicesQA" not in _named(report, DeadCodeKind.UNUSED_EXPORT)
    assert "SystemIndicesQA.java" not in _named(report, DeadCodeKind.UNREACHABLE_FILE)


def test_jmh_class_with_state_after_another_annotation_is_not_reported(tmp_path: Path):
    report = _report(tmp_path)
    assert "SortBench" not in _named(report, DeadCodeKind.UNUSED_EXPORT)
    assert "SortBench.java" not in _named(report, DeadCodeKind.UNREACHABLE_FILE)


def test_type_used_by_bare_name_is_not_reported(tmp_path: Path):
    exports = _named(_report(tmp_path), DeadCodeKind.UNUSED_EXPORT)
    assert "FilterType" not in exports  # from its package
    assert "Mode" not in exports  # further down its own file


def test_gradle_kotlin_script_is_not_reported(tmp_path: Path):
    unreachable = _named(_report(tmp_path), DeadCodeKind.UNREACHABLE_FILE)
    assert "java-library.sample.gradle.kts" not in unreachable


def test_class_nothing_names_is_still_reported(tmp_path: Path):
    assert "Orphan" in _named(_report(tmp_path), DeadCodeKind.UNUSED_EXPORT)


def test_class_annotations_reach_the_symbol():
    from repowise.core.ingestion.models import FileInfo

    source = b"package a;\n\n@Fork(1)\n@State(Scope.Thread)\npublic class C {\n}\n"
    info = FileInfo(
        path="C.java", abs_path="C.java", language="java", size_bytes=len(source),
        git_hash="", last_modified=None, is_test=False, is_config=False, is_api_contract=False,
        is_entry_point=False,
    )
    [cls] = [s for s in ASTParser().parse_file(info, source).symbols if s.name == "C"]
    assert "@State(Scope.Thread)" in cls.decorators[0]
