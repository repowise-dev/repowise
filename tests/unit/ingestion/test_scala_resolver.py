"""Unit tests for the Scala SBT/Mill-aware import resolver."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.scala import resolve_scala_import
from repowise.core.ingestion.resolvers.scala_build import build_scala_index


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


def _make_sbt_project(repo: Path, project_dir: str, package: str, class_name: str) -> str:
    src = repo / project_dir / "src" / "main" / "scala" / package.replace(".", "/")
    src.mkdir(parents=True)
    file_path = src / f"{class_name}.scala"
    file_path.write_text(f"package {package}\n\nclass {class_name}\n")
    return file_path.relative_to(repo).as_posix()


def _make_mill_module(repo: Path, module_name: str, package: str, class_name: str) -> str:
    src = repo / module_name / "src" / package.replace(".", "/")
    src.mkdir(parents=True)
    file_path = src / f"{class_name}.scala"
    file_path.write_text(f"package {package}\n\nclass {class_name}\n")
    return file_path.relative_to(repo).as_posix()


class TestScalaIndex:
    def test_sbt_subprojects_detected(self, tmp_path: Path) -> None:
        (tmp_path / "build.sbt").write_text(
            'lazy val core = project.in(file("core"))\n'
            'lazy val util = project.in(file("util"))\n'
        )
        rel = _make_sbt_project(tmp_path, "core", "com.example", "Engine")
        index = build_scala_index(tmp_path)
        assert index.build_tool == "sbt"
        assert "core" in index.projects
        assert rel in index.package_to_files["com.example"]

    def test_mill_modules_detected(self, tmp_path: Path) -> None:
        (tmp_path / "build.sc").write_text(
            "import mill._\n"
            "object core extends ScalaModule { def scalaVersion = T(\"3.0.0\") }\n"
        )
        rel = _make_mill_module(tmp_path, "core", "com.example", "Engine")
        index = build_scala_index(tmp_path)
        assert index.build_tool == "mill"
        assert "core" in index.projects
        assert rel in index.package_to_files["com.example"]

    def test_resolves_via_sbt_index(self, tmp_path: Path) -> None:
        (tmp_path / "build.sbt").write_text('lazy val core = project.in(file("core"))\n')
        rel = _make_sbt_project(tmp_path, "core", "com.example", "Engine")
        ctx = _ctx(tmp_path, [rel])
        result = resolve_scala_import("com.example.Engine", "main.scala", ctx)
        assert result == rel

    def test_no_build_file_falls_through(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Foo.scala").write_text("class Foo\n")
        ctx = _ctx(tmp_path, ["src/Foo.scala"])
        result = resolve_scala_import("com.example.Foo", "main.scala", ctx)
        assert result == "src/Foo.scala"
