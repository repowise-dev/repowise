"""Unit tests for the JVM Gradle index builder."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.jvm_gradle import (
    JvmGradleIndex,
    build_jvm_gradle_index,
)


def _ctx(repo: Path, paths: list[str]) -> ResolverContext:
    path_set = set(paths)
    stem_map: dict[str, list[str]] = {}
    for p in paths:
        stem = p.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        stem_map.setdefault(stem, []).append(p)
    return ResolverContext(
        path_set=path_set,
        stem_map=stem_map,
        graph=nx.DiGraph(),
        repo_path=repo,
    )


def _make_java_class(repo: Path, module: str, package: str, class_name: str) -> str:
    pkg_dir = package.replace(".", "/")
    src_dir = repo / module / "src" / "main" / "java" / pkg_dir
    src_dir.mkdir(parents=True, exist_ok=True)
    file_path = src_dir / f"{class_name}.java"
    file_path.write_text(f"package {package};\n\npublic class {class_name} {{}}\n")
    return file_path.relative_to(repo).as_posix()


def _make_kotlin_class(repo: Path, module: str, package: str, class_name: str) -> str:
    pkg_dir = package.replace(".", "/")
    src_dir = repo / module / "src" / "main" / "kotlin" / pkg_dir
    src_dir.mkdir(parents=True, exist_ok=True)
    file_path = src_dir / f"{class_name}.kt"
    file_path.write_text(f"package {package}\n\nclass {class_name}\n")
    return file_path.relative_to(repo).as_posix()


class TestJvmGradleIndex:
    def test_discovers_java_and_kotlin_files(self, tmp_path: Path) -> None:
        (tmp_path / "settings.gradle.kts").write_text('include("app")\n')
        (tmp_path / "build.gradle.kts").write_text("// root\n")
        java_path = _make_java_class(tmp_path, "app", "com.example", "Foo")
        kt_path = _make_kotlin_class(tmp_path, "app", "com.example", "Bar")

        index = build_jvm_gradle_index(tmp_path)

        assert "app" in index.modules
        pkg_files = index.package_to_files.get("com.example", [])
        assert java_path in pkg_files
        assert kt_path in pkg_files

    def test_lookup_class_java(self, tmp_path: Path) -> None:
        (tmp_path / "settings.gradle.kts").write_text('include("lib")\n')
        (tmp_path / "build.gradle.kts").write_text("// root\n")
        java_path = _make_java_class(tmp_path, "lib", "com.example.lib", "Engine")

        index = build_jvm_gradle_index(tmp_path)
        matches = index.lookup_class("com.example.lib.Engine")
        assert java_path in matches

    def test_lookup_class_kotlin(self, tmp_path: Path) -> None:
        (tmp_path / "settings.gradle.kts").write_text('include("lib")\n')
        (tmp_path / "build.gradle.kts").write_text("// root\n")
        kt_path = _make_kotlin_class(tmp_path, "lib", "com.example.lib", "Engine")

        index = build_jvm_gradle_index(tmp_path)
        matches = index.lookup_class("com.example.lib.Engine")
        assert kt_path in matches

    def test_files_in_package(self, tmp_path: Path) -> None:
        (tmp_path / "settings.gradle.kts").write_text('include("app")\n')
        (tmp_path / "build.gradle.kts").write_text("// root\n")
        _make_java_class(tmp_path, "app", "com.example.app", "A")
        _make_java_class(tmp_path, "app", "com.example.app", "B")

        index = build_jvm_gradle_index(tmp_path)
        files = index.files_in_package("com.example.app")
        assert len(files) == 2

    def test_single_module_fallback(self, tmp_path: Path) -> None:
        (tmp_path / "build.gradle.kts").write_text("// single\n")
        java_path = _make_java_class(tmp_path, "", "com.example", "App")

        index = build_jvm_gradle_index(tmp_path)
        assert "<root>" in index.modules
        assert index.lookup_class("com.example.App") == [java_path]

    def test_groovy_include(self, tmp_path: Path) -> None:
        (tmp_path / "settings.gradle").write_text("include 'core'\n")
        (tmp_path / "build.gradle").write_text("// root\n")
        _make_java_class(tmp_path, "core", "com.example", "Core")

        index = build_jvm_gradle_index(tmp_path)
        assert "core" in index.modules

    def test_detects_test_source_sets(self, tmp_path: Path) -> None:
        (tmp_path / "build.gradle.kts").write_text("// root\n")
        # Create integrationTest source set via directory convention
        it_dir = tmp_path / "src" / "integrationTest" / "java" / "com" / "example"
        it_dir.mkdir(parents=True)
        (it_dir / "IT.java").write_text("package com.example;\n\npublic class IT {}\n")

        index = build_jvm_gradle_index(tmp_path)
        project = index.projects.get("<root>")
        assert project is not None
        it_ss = project.source_sets.get("integrationTest")
        assert it_ss is not None
        assert it_ss.is_test

    def test_project_dir_override(self, tmp_path: Path) -> None:
        (tmp_path / "settings.gradle.kts").write_text(
            'include("mymod")\n'
            'project(":mymod").projectDir = file("custom/location")\n'
        )
        (tmp_path / "build.gradle.kts").write_text("// root\n")
        java_path = _make_java_class(tmp_path, "custom/location", "com.example", "Custom")

        index = build_jvm_gradle_index(tmp_path)
        matches = index.lookup_class("com.example.Custom")
        assert java_path in matches
