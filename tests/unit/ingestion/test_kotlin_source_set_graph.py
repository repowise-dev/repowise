"""The source-set dependencies a Kotlin Multiplatform call is ranked by."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion.resolvers.jvm_gradle import (
    build_source_set_graph,
    declaration_rank,
)


def _paths(module: str, *source_sets: str) -> list[str]:
    return [f"{module}/src/{name}/kotlin/p/F.kt" for name in source_sets]


def _visible(graph, module: str, name: str) -> dict[str, int]:  # type: ignore[no-untyped-def]
    return graph.modules[module].visible_from(name)


def test_the_default_hierarchy_template_links_the_source_sets(tmp_path: Path) -> None:
    sets = ("commonMain", "nativeMain", "appleMain", "linuxMain", "jvmMain", "jvmTest")
    graph = build_source_set_graph(_paths("lib", *sets), tmp_path)

    assert _visible(graph, "lib", "linuxMain") == {"linuxMain": 0, "nativeMain": 1, "commonMain": 2}
    assert _visible(graph, "lib", "appleMain") == {"appleMain": 0, "nativeMain": 1, "commonMain": 2}
    assert set(_visible(graph, "lib", "jvmTest")) == {"jvmTest", "jvmMain", "commonTest", "commonMain"}
    assert "jvmMain" not in _visible(graph, "lib", "linuxMain")
    assert "appleMain" not in _visible(graph, "lib", "linuxMain")


def test_target_specific_sets_sit_under_their_group(tmp_path: Path) -> None:
    graph = build_source_set_graph(_paths("lib", "commonMain", "iosArm64Main", "linuxX64Test"), tmp_path)

    assert list(_visible(graph, "lib", "iosArm64Main")) == [
        "iosArm64Main", "iosMain", "appleMain", "nativeMain", "commonMain",
    ]  # fmt: skip
    assert {"linuxX64Main", "linuxMain", "nativeMain"} <= set(_visible(graph, "lib", "linuxX64Test"))


def test_a_test_set_sees_its_main_set_and_common_test_sees_common_main(tmp_path: Path) -> None:
    graph = build_source_set_graph(_paths("lib", "commonMain", "commonTest"), tmp_path)

    assert _visible(graph, "lib", "commonTest") == {"commonTest": 0, "commonMain": 1}


@pytest.mark.parametrize("script", ["build.gradle.kts", "build.gradle"])
def test_an_explicit_depends_on_adds_an_edge_the_template_does_not_have(
    tmp_path: Path, script: str
) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / script).write_text(
        "kotlin {\n  sourceSets {\n"
        "    val zlibMain by creating { dependsOn(commonMain) }\n"
        "    val jvmMain by getting {\n      dependsOn(zlibMain)\n    }\n"
        "    linuxMain.dependsOn(zlibMain)\n"
        "  }\n}\n"
    )
    graph = build_source_set_graph(
        _paths("lib", "commonMain", "zlibMain", "jvmMain", "linuxMain"), tmp_path
    )

    assert _visible(graph, "lib", "jvmMain") == {"jvmMain": 0, "zlibMain": 1, "commonMain": 1}
    assert "zlibMain" in _visible(graph, "lib", "linuxMain")
    assert "zlibMain" not in _visible(graph, "lib", "commonMain")


def test_depends_on_forms_in_the_build_script(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "build.gradle.kts").write_text(
        'kotlin.sourceSets.getByName("aMain") { dependsOn(sourceSets["zMain"]) }\n'
        "sourceSets.bMain.dependsOn(sourceSets.getByName(\"zMain\"))\n"
        "// cMain.dependsOn(zMain)\n"
        "/* dMain.dependsOn(zMain) */\n"
        'tasks.named("eMain") { dependsOn(zMain) }\n'
        'compileKotlin.dependsOn(generateSources)\n'
        'tasks.named("jvmTest") { dependsOn("prepare") }\n'
    )
    sets = ("commonMain", "aMain", "bMain", "cMain", "dMain", "eMain", "zMain")
    graph = build_source_set_graph(_paths("lib", *sets), tmp_path)

    assert "zMain" in _visible(graph, "lib", "aMain")
    assert "zMain" in _visible(graph, "lib", "bMain")
    for ignored in ("cMain", "dMain", "eMain"):  # a comment, or a task's dependsOn
        assert "zMain" not in _visible(graph, "lib", ignored)


def test_a_custom_named_target_sits_under_common(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "build.gradle.kts").write_text('kotlin { jvm("desktop") }\n')
    graph = build_source_set_graph(_paths("app", "commonMain", "desktopMain", "desktopTest"), tmp_path)

    assert _visible(graph, "app", "desktopTest") == {
        "desktopTest": 0, "desktopMain": 1, "commonTest": 1, "commonMain": 2,
    }  # fmt: skip


def test_a_script_the_reader_cannot_follow_falls_back_to_the_template(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "build.gradle.kts").write_text(
        'createSourceSet("jvmMain", parent = commonMain)\n'
    )
    graph = build_source_set_graph(_paths("lib", "commonMain", "jvmMain", "linuxMain"), tmp_path)

    assert _visible(graph, "lib", "jvmMain") == {"jvmMain": 0, "commonMain": 1}
    assert "jvmMain" not in _visible(graph, "lib", "linuxMain")


def test_a_plain_gradle_module_is_not_multiplatform(tmp_path: Path) -> None:
    paths = _paths("app", "main", "test", "integrationTest", "androidTest")
    graph = build_source_set_graph(paths, tmp_path)

    assert graph.modules == {}


def test_the_multiplatform_plugin_marks_a_module(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "build.gradle.kts").write_text('plugins { kotlin("multiplatform") }\n')
    graph = build_source_set_graph(_paths("lib", "customMain"), tmp_path)

    assert "lib" in graph.modules


class TestDeclarationRank:
    def _graph(self, tmp_path: Path):  # type: ignore[no-untyped-def]
        sets = ("commonMain", "nativeMain", "appleMain", "linuxMain", "jvmMain", "jvmTest")
        return build_source_set_graph(_paths("lib", *sets) + _paths("app", "main", "test"), tmp_path)

    def _rank(self, graph, importer: str, candidate: str, expect: bool = False):  # type: ignore[no-untyped-def]
        f = "lib/src/{}/kotlin/p/F.kt"
        return declaration_rank(f.format(importer), f.format(candidate), expect, graph)

    def test_own_set_then_nearest_actual_then_expect(self, tmp_path: Path) -> None:
        graph = self._graph(tmp_path)

        own = self._rank(graph, "linuxMain", "linuxMain")
        native_actual = self._rank(graph, "linuxMain", "nativeMain")
        common_expect = self._rank(graph, "linuxMain", "commonMain", expect=True)
        assert own == 0
        assert own < native_actual < common_expect

    def test_a_set_the_importer_cannot_see_is_not_a_candidate(self, tmp_path: Path) -> None:
        graph = self._graph(tmp_path)

        assert self._rank(graph, "linuxMain", "appleMain") is None
        assert self._rank(graph, "linuxMain", "jvmMain") is None
        assert self._rank(graph, "commonMain", "jvmMain") is None

    def test_a_test_set_ranks_its_main_actual_ahead_of_the_expect(self, tmp_path: Path) -> None:
        graph = self._graph(tmp_path)

        assert self._rank(graph, "jvmTest", "jvmMain") < self._rank(
            graph, "jvmTest", "commonMain", expect=True
        )

    def test_without_a_graph_the_rank_is_the_plain_one(self, tmp_path: Path) -> None:
        f = "lib/src/{}/kotlin/p/F.kt"

        assert declaration_rank(f.format("a"), f.format("a"), False) == 0
        assert declaration_rank(f.format("a"), f.format("b"), True) == 1
        assert declaration_rank(f.format("a"), f.format("b"), False) == 2

    def test_a_plain_gradle_module_and_other_modules_keep_the_plain_rank(
        self, tmp_path: Path
    ) -> None:
        graph = self._graph(tmp_path)
        app = "app/src/{}/kotlin/m/H.kt"
        lib = "lib/src/jvmMain/kotlin/p/F.kt"

        assert declaration_rank(app.format("test"), app.format("main"), False, graph) == 2
        assert declaration_rank(app.format("main"), lib, False, graph) == 2  # another module
        assert declaration_rank(lib, app.format("main"), True, graph) == 1
