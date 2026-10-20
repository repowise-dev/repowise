"""Compose previews and Dagger/Hilt modules are loaded by tooling, never imported.

Shapes from nowinandroid: previews under ``@Preview``, a preset, a qualified
``@Preview`` and the repo's own multipreview annotation (itself a misparse the
graph never holds, so it is read from source), and a Hilt ``@Module`` object
with ``@Provides``/``@Binds`` members. Every one was reported dead, the
previews as deletion-ready exports.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.analyzer import _is_framework_registered
from repowise.core.analysis.dead_code.meta_annotations import MetaAnnotations
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_UI = "ui/src/main/kotlin/org/sample/ui"

_FILES = {
    f"{_UI}/ThemePreviews.kt": (
        "package org.sample.ui\n\n"
        "import androidx.compose.ui.tooling.preview.Preview\n\n"
        '@Preview(name = "Light theme")\n'
        '@Preview(name = "Dark theme", uiMode = mode(1))\n'
        "annotation class ThemePreviews\n\n"
        "@ThemePreviews\n"
        "annotation class NestedPreviews\n\n"
        "annotation class NotAPreview\n"
    ),
    f"{_UI}/Button.kt": (
        "package org.sample.ui\n\n"
        "@ThemePreviews\n@Composable\nfun ButtonThemePreview() {}\n\n"
        "@NestedPreviews\n@Composable\nfun ButtonNestedPreview() {}\n\n"
        "@Preview\n@Composable\nfun ButtonPlainPreview() {}\n\n"
        "@PreviewLightDark\n@Composable\nfun ButtonPresetPreview() {}\n\n"
        "@androidx.compose.ui.tooling.preview.Preview\n@Composable\n"
        "fun ButtonQualifiedPreview() {}\n\n"
        "@NotAPreview\n@Composable\nfun ButtonUnusedComposable() {}\n\n"
        "class UnusedHelper {\n    fun help() {}\n}\n"
    ),
    "app/src/main/kotlin/org/sample/app/di/JankStatsModule.kt": (
        "package org.sample.app.di\n\n"
        "@Module\n@InstallIn(ActivityComponent::class)\n"
        "object JankStatsModule {\n"
        "    @Provides\n    fun providesListener(): String = \"\"\n\n"
        "    @Binds\n    fun bindsListener(x: Int): Int = x\n"
        "}\n"
    ),
}


def _report(root: Path, files: dict[str, str] | None = None):
    files = _FILES if files is None else files
    source_map = {rel: text.encode() for rel, text in files.items()}
    for rel, source in source_map.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=root)
    for fi in FileTraverser(root).traverse():
        builder.add_file(parser.parse_file(fi, source_map[fi.path]))
    builder.set_source_map(source_map)
    graph = builder.build()
    builder.add_framework_edges([])
    analyzer = DeadCodeAnalyzer(graph, git_meta_map={}, source_map=source_map, repo_root=root)
    return analyzer.analyze({"min_confidence": 0.0})


def _of(report, kind: DeadCodeKind) -> set[str]:
    return {f.symbol_name or Path(f.file_path).name for f in report.findings if f.kind is kind}


def test_previews_are_never_flagged(tmp_path: Path):
    unused = _of(_report(tmp_path), DeadCodeKind.UNUSED_EXPORT)
    assert unused.isdisjoint(
        {
            "ButtonThemePreview",
            "ButtonNestedPreview",
            "ButtonPlainPreview",
            "ButtonPresetPreview",
            "ButtonQualifiedPreview",
        }
    )
    # A repo annotation without a preview on it registers nothing.
    assert {"ButtonUnusedComposable", "UnusedHelper"} <= unused


def test_hilt_module_is_loaded_by_the_component(tmp_path: Path):
    report = _report(tmp_path)
    assert "JankStatsModule.kt" not in _of(report, DeadCodeKind.UNREACHABLE_FILE)
    flagged = _of(report, DeadCodeKind.UNUSED_EXPORT) | _of(report, DeadCodeKind.UNUSED_INTERNAL)
    assert flagged.isdisjoint({"JankStatsModule", "providesListener", "bindsListener"})


# Two annotation classes named ``ThemeShots``: only ``org.sample.ui``'s is a
# multipreview. A use counts only where that one is the class in scope.
_UI_SHOTS = "package org.sample.ui\n\n@Preview\nannotation class ThemeShots\n"
_UI_FQN = "org.sample.ui.ThemeShots"
_OTHER_SHOTS = "package org.sample.other\n\nannotation class ThemeShots\n"
_USERS = {
    "app/SamePackage.kt": "package org.sample.ui\n",
    "app/Imported.kt": "package org.sample.app\n\nimport org.sample.ui.ThemeShots\n",
    "app/Star.kt": "package org.sample.app\n\nimport org.sample.ui.*\n",
    "app/Aliased.kt": "package org.sample.app\n\nimport org.sample.ui.ThemeShots as UiShots\n",
    "app/OtherImport.kt": "package org.sample.app\n\nimport org.sample.other.ThemeShots\n",
    "app/OtherPackage.kt": "package org.sample.other\n",
    "app/Unrelated.kt": "package org.sample.app\n",
}


def test_same_named_annotation_counts_only_where_it_is_in_scope():
    sources = {
        "ui/ThemeShots.kt": _UI_SHOTS.encode(),
        "other/ThemeShots.kt": _OTHER_SHOTS.encode(),
        **{path: text.encode() for path, text in _USERS.items()},
    }
    meta = MetaAnnotations(sources, _is_framework_registered)
    assert meta.visible_in("app/SamePackage.kt") == {"ThemeShots", _UI_FQN}
    assert meta.visible_in("app/Imported.kt") == {"ThemeShots", _UI_FQN}
    assert meta.visible_in("app/Star.kt") == {"ThemeShots", _UI_FQN}
    assert meta.visible_in("app/Aliased.kt") == {"UiShots", _UI_FQN}
    for path in ("app/OtherImport.kt", "app/OtherPackage.kt", "app/Unrelated.kt"):
        assert meta.visible_in(path) == {_UI_FQN}, path
    # Unique repo-wide, the name counts wherever it is used.
    unique = MetaAnnotations({"ui/ThemeShots.kt": _UI_SHOTS.encode()}, _is_framework_registered)
    assert unique.visible_in("app/Unrelated.kt") == {"ThemeShots", _UI_FQN}


def test_qualified_use_matches_by_fully_qualified_name():
    sources = {
        "ui/ThemeShots.kt": _UI_SHOTS.encode(),
        "other/ThemeShots.kt": _OTHER_SHOTS.encode(),
        "app/Unrelated.kt": _USERS["app/Unrelated.kt"].encode(),
        "app/OtherPackage.kt": _USERS["app/OtherPackage.kt"].encode(),
    }
    meta = MetaAnnotations(sources, _is_framework_registered)
    for path in ("app/Unrelated.kt", "app/OtherPackage.kt"):
        visible = meta.visible_in(path)
        # Out of scope by simple name, the preview one still counts qualified.
        assert _is_framework_registered([f"@{_UI_FQN}"], visible), path
        assert not _is_framework_registered(["@org.sample.other.ThemeShots"], visible), path
    # In the other class's package, the bare name means the non-preview one.
    assert not _is_framework_registered(["@ThemeShots"], meta.visible_in("app/OtherPackage.kt"))


# Glide's annotation processor generates ``GeneratedAppGlideModule``, which
# instantiates the annotated module class. Nothing in source names it, so both
# the file and the class were reported dead (AntennaPod's ``ApGlideModule``).
_GLIDE_ANNOTATED = "app/src/main/java/com/example/glide/annotated"
_GLIDE_BARE = "app/src/main/java/com/example/glide/bare"

_GLIDE_FILES = {
    f"{_GLIDE_ANNOTATED}/ApGlideModule.java": (
        "package com.example.glide.annotated;\n"

        "\n"

        "@GlideModule\n"

        "public class ApGlideModule extends AppGlideModule {\n"

        "    @Override\n"

        "    public void applyOptions(Context context, GlideBuilder builder) {\n"

        "        builder.setLogLevel(6);\n"

        "    }\n"

        "}\n"

    ),
    f"{_GLIDE_BARE}/BareGlideModule.java": (
        "package com.example.glide.bare;\n"

        "\n"

        "public class BareGlideModule extends AppGlideModule {\n"

        "    @Override\n"

        "    public void applyOptions(Context context, GlideBuilder builder) {\n"

        "        builder.setLogLevel(6);\n"

        "    }\n"

        "}\n"

    ),
}


def test_glide_module_is_instantiated_by_the_generated_component(tmp_path: Path):
    report = _report(tmp_path, _GLIDE_FILES)
    flagged = _of(report, DeadCodeKind.UNUSED_EXPORT) | _of(report, DeadCodeKind.UNUSED_INTERNAL)
    assert "ApGlideModule.java" not in _of(report, DeadCodeKind.UNREACHABLE_FILE)
    assert "ApGlideModule" not in flagged
    # Glide ignores a module class without the annotation, so it stays reported.
    assert "BareGlideModule.java" in _of(report, DeadCodeKind.UNREACHABLE_FILE)
    assert "BareGlideModule" in flagged


def test_framework_registration_accepts_the_glide_annotation():
    assert _is_framework_registered(["@GlideModule"])
