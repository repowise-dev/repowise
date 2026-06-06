"""Unit tests for the Scala SBT/Mill-aware import resolver."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.scala import (
    resolve_scala_import,
    resolve_scala_import_all,
)
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


def _make_scala(repo: Path, rel_path: str, package: str, decl: str) -> str:
    full = repo / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(f"package {package}\n\n{decl}\n")
    return rel_path


class TestScalaWorkspaceResolution:
    def test_package_fqn_resolution(self, tmp_path: Path) -> None:
        a = _make_scala(tmp_path, "src/main/scala/com/foo/Engine.scala", "com.foo", "class Engine")
        ctx = _ctx(tmp_path, [a])
        result = resolve_scala_import("com.foo.Engine", "Main.scala", ctx)
        assert result == a

    def test_type_in_differently_named_file(self, tmp_path: Path) -> None:
        # FQN lookup keys on declared type names, not stems.
        a = _make_scala(
            tmp_path, "src/main/scala/com/foo/models.scala", "com.foo",
            "case class User(name: String)\ncase class Order(id: Int)",
        )
        ctx = _ctx(tmp_path, [a])
        assert resolve_scala_import("com.foo.User", "Main.scala", ctx) == a
        assert resolve_scala_import("com.foo.Order", "Main.scala", ctx) == a

    def test_trait_and_object_resolution(self, tmp_path: Path) -> None:
        a = _make_scala(tmp_path, "src/main/scala/com/foo/Api.scala", "com.foo", "trait Api")
        b = _make_scala(tmp_path, "src/main/scala/com/foo/Defaults.scala", "com.foo", "object Defaults")
        ctx = _ctx(tmp_path, [a, b])
        assert resolve_scala_import("com.foo.Api", "Main.scala", ctx) == a
        assert resolve_scala_import("com.foo.Defaults", "Main.scala", ctx) == b

    def test_wildcard_fans_out_to_package(self, tmp_path: Path) -> None:
        a = _make_scala(tmp_path, "src/main/scala/com/foo/A.scala", "com.foo", "class A")
        b = _make_scala(tmp_path, "src/main/scala/com/foo/B.scala", "com.foo", "class B")
        ctx = _ctx(tmp_path, [a, b])
        targets = resolve_scala_import_all("com.foo.*", "Main.scala", ctx)
        assert set(targets) == {a, b}
        # Scala 2 underscore form normalises identically.
        targets = resolve_scala_import_all("com.foo._", "Main.scala", ctx)
        assert set(targets) == {a, b}

    def test_package_import_fans_out(self, tmp_path: Path) -> None:
        a = _make_scala(tmp_path, "src/main/scala/com/foo/A.scala", "com.foo", "class A")
        ctx = _ctx(tmp_path, [a])
        targets = resolve_scala_import_all("com.foo", "Main.scala", ctx)
        assert targets == (a,)

    def test_stdlib_dropped(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, [])
        assert resolve_scala_import_all("scala.collection.mutable", "Main.scala", ctx) == ()
        assert resolve_scala_import_all("java.util.UUID", "Main.scala", ctx) == ()
        assert resolve_scala_import_all("scala.concurrent.Future", "Main.scala", ctx) == ()

    def test_unknown_external(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, [])
        result = resolve_scala_import("org.typelevel.cats.Monad", "Main.scala", ctx)
        assert result == "external:org.typelevel.cats.Monad"

    def test_cross_language_scala_to_java(self, tmp_path: Path) -> None:
        j = tmp_path / "src/main/java/com/foo/Legacy.java"
        j.parent.mkdir(parents=True)
        j.write_text("package com.foo;\npublic class Legacy {}\n")
        ctx = _ctx(tmp_path, ["src/main/java/com/foo/Legacy.java"])
        result = resolve_scala_import("com.foo.Legacy", "Main.scala", ctx)
        assert result == "src/main/java/com/foo/Legacy.java"

    def test_chained_package_clauses(self, tmp_path: Path) -> None:
        full = tmp_path / "core/src/main/scala/tools/Runner.scala"
        full.parent.mkdir(parents=True)
        full.write_text("package org.example\npackage tools\n\nclass Runner\n")
        ctx = _ctx(tmp_path, ["core/src/main/scala/tools/Runner.scala"])
        result = resolve_scala_import("org.example.tools.Runner", "Main.scala", ctx)
        assert result == "core/src/main/scala/tools/Runner.scala"

    def test_multi_module_resolution(self, tmp_path: Path) -> None:
        (tmp_path / "build.sbt").write_text(
            'lazy val core = project.in(file("core"))\n'
            'lazy val app = project.in(file("app"))\n'
        )
        core = _make_scala(
            tmp_path, "core/src/main/scala/com/example/core/Engine.scala",
            "com.example.core", "class Engine",
        )
        app = _make_scala(
            tmp_path, "app/src/main/scala/com/example/app/Main.scala",
            "com.example.app", "object Main extends App",
        )
        ctx = _ctx(tmp_path, [core, app])
        result = resolve_scala_import("com.example.core.Engine", app, ctx)
        assert result == core
