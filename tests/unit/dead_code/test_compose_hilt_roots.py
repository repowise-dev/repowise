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
