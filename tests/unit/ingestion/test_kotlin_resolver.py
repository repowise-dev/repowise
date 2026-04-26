"""Unit tests for the Kotlin Gradle-aware import resolver."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.kotlin import resolve_kotlin_import
from repowise.core.ingestion.resolvers.kotlin_gradle import build_kotlin_index


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


def _make_module(repo: Path, name: str, package: str, class_name: str) -> str:
    src_dir = repo / name / "src" / "main" / "kotlin" / package.replace(".", "/")
    src_dir.mkdir(parents=True)
    file_path = src_dir / f"{class_name}.kt"
    file_path.write_text(f"package {package}\n\nclass {class_name}\n")
    return file_path.relative_to(repo).as_posix()


class TestKotlinIndex:
    def test_settings_gradle_subprojects(self, tmp_path: Path) -> None:
        (tmp_path / "settings.gradle.kts").write_text(
            'include("app", "core", "feature-foo")\n'
        )
        (tmp_path / "build.gradle.kts").write_text("// root\n")
        # Create app module
        _make_module(tmp_path, "app", "com.example.app", "MainActivity")
        _make_module(tmp_path, "core", "com.example.core", "Engine")
        index = build_kotlin_index(tmp_path)
        assert "app" in index.modules
        assert "core" in index.modules
        assert "com.example.app" in index.package_to_files
        assert "com.example.core" in index.package_to_files

    def test_resolves_class_to_module_file(self, tmp_path: Path) -> None:
        (tmp_path / "settings.gradle").write_text('include "core"\n')
        (tmp_path / "build.gradle").write_text("// root\n")
        rel = _make_module(tmp_path, "core", "com.example", "Engine")
        ctx = _ctx(tmp_path, [rel])
        result = resolve_kotlin_import("com.example.Engine", "main.kt", ctx)
        assert result == rel

    def test_falls_through_without_gradle(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Foo.kt").write_text("class Foo\n")
        ctx = _ctx(tmp_path, ["src/Foo.kt"])
        result = resolve_kotlin_import("com.example.Foo", "main.kt", ctx)
        assert result == "src/Foo.kt"

    def test_single_module_root_build_gradle(self, tmp_path: Path) -> None:
        (tmp_path / "build.gradle.kts").write_text("// single module\n")
        src = tmp_path / "src" / "main" / "kotlin" / "com" / "example"
        src.mkdir(parents=True)
        (src / "Util.kt").write_text("package com.example\n\nclass Util\n")
        ctx = _ctx(tmp_path, ["src/main/kotlin/com/example/Util.kt"])
        result = resolve_kotlin_import("com.example.Util", "main.kt", ctx)
        assert result == "src/main/kotlin/com/example/Util.kt"
