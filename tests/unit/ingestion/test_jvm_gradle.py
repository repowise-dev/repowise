"""Unit tests for the JVM Gradle index builder."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.jvm_gradle import (
    build_jvm_gradle_index,
    parse_settings,
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
    @pytest.mark.parametrize("repo_arg", ["project", "./project", "parent/../project"])
    def test_relative_repo_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo_arg: str
    ) -> None:
        repo = tmp_path / "project"
        repo.mkdir()
        (tmp_path / "parent").mkdir()
        (repo / "settings.gradle.kts").write_text(
            'include("app", ":libs:core")\n'
            'project(":libs:core").projectDir = file("custom/core")\n'
        )
        (repo / "build.gradle.kts").write_text("// root\n")
        app_path = _make_java_class(repo, "app", "com.example", "App")
        core_path = _make_kotlin_class(repo, "custom/core", "com.example", "Core")
        monkeypatch.chdir(tmp_path)

        index = build_jvm_gradle_index(Path(repo_arg))

        assert index.projects["app"].root_dir == "app"
        assert index.projects["libs:core"].root_dir == "custom/core"
        assert index.lookup_class("com.example.App") == [app_path]
        assert index.lookup_class("com.example.Core") == [core_path]

    @pytest.mark.parametrize("relative_repo", [False, True])
    @pytest.mark.parametrize("absolute_override", [False, True])
    def test_project_dir_outside_repo(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        relative_repo: bool,
        absolute_override: bool,
    ) -> None:
        repo = tmp_path / "project"
        repo.mkdir()
        external = tmp_path / "shared"
        external.mkdir()
        override = external.as_posix() if absolute_override else "../shared"
        (repo / "settings.gradle.kts").write_text(
            'include("shared", "app")\n'
            f'project(":shared").projectDir = file("{override}")\n'
        )
        app_path = _make_java_class(repo, "app", "com.example", "App")
        _make_java_class(external, "", "com.external", "Shared")
        monkeypatch.chdir(tmp_path)

        index = build_jvm_gradle_index(Path("project") if relative_repo else repo)

        assert index.projects["shared"].root_dir == "../shared"
        assert "main" in index.projects["shared"].source_sets
        assert index.projects["app"].root_dir == "app"
        assert index.lookup_class("com.example.App") == [app_path]
        assert index.lookup_class("com.external.Shared") == []

    @pytest.mark.parametrize("relative_repo", [False, True])
    def test_symlinked_module_outside_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative_repo: bool
    ) -> None:
        repo = tmp_path / "project"
        repo.mkdir()
        external = tmp_path / "shared"
        external.mkdir()
        _make_java_class(external, "", "com.external", "Shared")
        try:
            (repo / "shared").symlink_to(external, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"Directory symlinks unavailable: {exc}")
        (repo / "settings.gradle.kts").write_text('include("shared", "app")\n')
        app_path = _make_java_class(repo, "app", "com.example", "App")
        monkeypatch.chdir(tmp_path)

        index = build_jvm_gradle_index(Path("project") if relative_repo else repo)

        assert index.projects["shared"].root_dir == "shared"
        assert "main" in index.projects["shared"].source_sets
        assert index.lookup_class("com.example.App") == [app_path]
        assert index.lookup_class("com.external.Shared") == []

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

    def test_plugin_detection_and_publishing(self, tmp_path: Path) -> None:
        from repowise.core.ingestion.resolvers.jvm_gradle import (
            JvmGradleProject,
            _detect_plugins,
            is_publishing_gradle_project,
        )

        (tmp_path / "lib").mkdir()
        (tmp_path / "lib" / "build.gradle.kts").write_text(
            "plugins {\n"
            "    `java-library`\n"
            "    `maven-publish`\n"
            "}\n"
        )
        lib_plugins = _detect_plugins(tmp_path / "lib")
        assert "java-library" in lib_plugins
        assert "maven-publish" in lib_plugins
        assert is_publishing_gradle_project(
            JvmGradleProject(id="lib", root_dir="lib", plugins=lib_plugins)
        )

        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "build.gradle.kts").write_text(
            'plugins {\n'
            '    id("application")\n'
            '    id("java-library.caffeine")\n'
            '}\n'
        )
        app_plugins = _detect_plugins(tmp_path / "app")
        assert "application" in app_plugins
        assert "java-library.caffeine" in app_plugins
        assert not is_publishing_gradle_project(
            JvmGradleProject(id="app", root_dir="app", plugins=app_plugins)
        )

        (tmp_path / "lib_comment").mkdir()
        (tmp_path / "lib_comment" / "build.gradle.kts").write_text(
            "plugins {\n"
            "    `java-library`\n"
            "    // application is not used here\n"
            "    /* application */\n"
            "    alias(libs.plugins.some.tool)\n"
            "}\n"
        )
        lib_comment_plugins = _detect_plugins(tmp_path / "lib_comment")
        assert "application" not in lib_comment_plugins
        assert "java-library" in lib_comment_plugins
        assert is_publishing_gradle_project(
            JvmGradleProject(id="lib_comment", root_dir="lib_comment", plugins=lib_comment_plugins)
        )


class TestParseSettingsInclude:
    """``parse_settings`` reads every module of an ``include`` statement (#3605)."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # Groovy space form, several modules on one line.
            ("include 'core', 'api', 'web'\n", ["core", "api", "web"]),
            # Groovy space form, list continued on the next line.
            ("include 'core',\n        'api'\n", ["core", "api"]),
            # Groovy parenthesised form with single quotes.
            ("include('core', 'api')\n", ["core", "api"]),
            # Kotlin DSL form (already worked; must keep working).
            ('include("core", "api")\n', ["core", "api"]),
        ],
    )
    def test_reads_every_module(self, tmp_path: Path, text: str, expected: list[str]) -> None:
        settings = tmp_path / "settings.gradle"
        settings.write_text(text)

        modules, _ = parse_settings(settings)
        assert modules == expected

    def test_mixed_quote_styles_and_leading_colon(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.gradle"
        settings.write_text("include ':app', \":lib:core\", 'web'\n")

        modules, _ = parse_settings(settings)
        assert modules == ["app", "lib:core", "web"]

    def test_include_build_is_not_a_module(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.gradle"
        settings.write_text("includeBuild(\"build-logic\")\ninclude 'a'\ninclude 'b', 'c'\n")

        modules, _ = parse_settings(settings)
        assert modules == ["a", "b", "c"]

    @pytest.mark.parametrize(
        "text",
        [
            # Line comment after a trailing comma, list continued on the next line.
            "include 'core', // the core module\n        'api'\n",
            # Block comment between two items.
            "include 'core', /* the API */ 'api'\n",
            # Block comment spanning lines inside a parenthesised list.
            'include(\n    "core", /* the\n    API */\n    "api"\n)\n',
        ],
    )
    def test_comment_between_items_keeps_the_list(self, tmp_path: Path, text: str) -> None:
        settings = tmp_path / "settings.gradle"
        settings.write_text(text)

        modules, _ = parse_settings(settings)
        assert modules == ["core", "api"]

    def test_commented_out_include_is_not_a_module(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings.gradle"
        settings.write_text("include 'core'\n// include 'legacy'\n/* include 'old' */\n")

        modules, _ = parse_settings(settings)
        assert modules == ["core"]

    def test_project_dir_override_applies_to_multi_name_include(self, tmp_path: Path) -> None:
        (tmp_path / "settings.gradle").write_text(
            "include 'core', 'api'\n"
            'project(":api").projectDir = file("modules/api")\n'
        )
        (tmp_path / "build.gradle").write_text("// root\n")
        _make_java_class(tmp_path, "core", "com.example.core", "Core")
        api_path = _make_java_class(tmp_path, "modules/api", "com.example.api", "Api")

        modules, overrides = parse_settings(tmp_path / "settings.gradle")
        assert modules == ["core", "api"]
        assert overrides == {"api": "modules/api"}

        index = build_jvm_gradle_index(tmp_path)
        assert "core" in index.modules
        assert "api" in index.modules
        assert api_path in index.lookup_class("com.example.api.Api")
