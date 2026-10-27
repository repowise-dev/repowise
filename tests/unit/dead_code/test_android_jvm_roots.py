"""Android and Gradle classes the build or the runtime starts, never an importer.

Shapes from nowinandroid and ktor-samples, every one reported dead: a
``build-logic`` convention plugin in the default package registered by
``implementationClass``, a ``com.android.test`` benchmark module, an app whose
launcher activity the manifest names relative to the Gradle ``namespace``, and
a sample module whose directory name makes all of it test code.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_LAUNCHER = (
    "<intent-filter>\n"
    '  <action android:name="android.intent.action.MAIN" />\n'
    '  <category android:name="android.intent.category.LAUNCHER" />\n'
    "</intent-filter>\n"
)

_FILES = {
    "settings.gradle.kts": (
        'include(":catalog")\ninclude(":legacy")\ninclude(":benchmarks")\n'
        'include(":orphan")\ninclude(":jwt-auth-tests")\n'
    ),
    # Convention plugins: one registered by bare name, one nothing names.
    "build-logic/settings.gradle.kts": 'include(":convention")\n',
    "build-logic/convention/build.gradle.kts": (
        "plugins { `kotlin-dsl` }\n"
        "gradlePlugin {\n  plugins {\n"
        '    register("androidLibrary") {\n'
        '      id = "sample.android.library"\n'
        '      implementationClass = "AndroidLibraryConventionPlugin"\n'
        "    }\n  }\n}\n"
    ),
    "build-logic/convention/src/main/kotlin/AndroidLibraryConventionPlugin.kt": (
        "class AndroidLibraryConventionPlugin {\n    fun apply() {}\n}\n"
    ),
    "build-logic/legacy/src/main/kotlin/UnregisteredPlugin.kt": (
        "class UnregisteredPlugin {\n    fun apply() {}\n}\n"
    ),
    # App whose manifest has no ``package``: ``.CatalogActivity`` completes
    # against the Gradle ``namespace``.
    "catalog/build.gradle.kts": (
        'plugins { id("com.android.application") }\n'
        'android {\n    namespace = "org.sample.catalog"\n}\n'
    ),
    "catalog/src/main/AndroidManifest.xml": (
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
        "  <application>\n"
        f'    <activity android:name=".CatalogActivity" android:exported="true">\n{_LAUNCHER}'
        "    </activity>\n  </application>\n</manifest>\n"
    ),
    "catalog/src/main/kotlin/org/sample/catalog/CatalogActivity.kt": (
        "package org.sample.catalog\n\nclass CatalogActivity {\n    fun onCreate() {}\n}\n"
    ),
    # Older layout: the manifest ``package`` qualifies a bare service name.
    "legacy/build.gradle": "apply plugin: 'com.android.application'\n",
    "legacy/src/main/AndroidManifest.xml": (
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    package="org.sample.legacy">\n'
        '  <application>\n    <service android:name="SyncService" />\n'
        "  </application>\n</manifest>\n"
    ),
    "legacy/src/main/java/org/sample/legacy/SyncService.java": (
        "package org.sample.legacy;\n\npublic class SyncService {\n}\n"
    ),
    # A ``com.android.test`` module, applied through a convention plugin.
    "benchmarks/build.gradle.kts": (
        "plugins {\n    alias(libs.plugins.sample.android.test)\n}\n"
        'android {\n    namespace = "org.sample.benchmarks"\n'
        '    targetProjectPath = ":catalog"\n}\n'
    ),
    "benchmarks/src/main/kotlin/org/sample/benchmarks/StartupBenchmark.kt": (
        "package org.sample.benchmarks\n\nclass StartupBenchmark {\n    fun startup() {}\n}\n"
    ),
    # A library module with a manifest that registers nothing.
    "orphan/build.gradle.kts": (
        'plugins { id("com.android.library") }\n'
        'android {\n    namespace = "org.sample.orphan"\n}\n'
    ),
    "orphan/src/main/AndroidManifest.xml": "<manifest />\n",
    "orphan/src/main/kotlin/org/sample/orphan/Helper.kt": (
        "package org.sample.orphan\n\nclass Helper {\n    fun help() {}\n}\n"
    ),
    # Every file under ``*-tests`` is test code; its symbols must not keep
    # the module a package candidate.
    "jwt-auth-tests/build.gradle.kts": "plugins { application }\n",
    "jwt-auth-tests/src/main/kotlin/org/sample/jwt/Main.kt": (
        "package org.sample.jwt\n\n"
        "fun main(args: Array<String>) {}\n\n"
        "fun Application.main() {\n    main(1)\n}\n\n"
        "fun Application.main(x: Int) {}\n"
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


def test_convention_plugin_registered_by_bare_name_is_a_root(tmp_path: Path):
    unreachable = _of(_report(tmp_path), DeadCodeKind.UNREACHABLE_FILE)
    assert "AndroidLibraryConventionPlugin.kt" not in unreachable
    assert "UnregisteredPlugin.kt" in unreachable


def test_manifest_components_resolve_relative_names(tmp_path: Path):
    report = _report(tmp_path)
    unreachable = _of(report, DeadCodeKind.UNREACHABLE_FILE)
    assert unreachable.isdisjoint({"CatalogActivity.kt", "SyncService.java"})
    assert "CatalogActivity" not in _of(report, DeadCodeKind.UNUSED_EXPORT)


def test_android_test_module_is_run_not_imported(tmp_path: Path):
    report = _report(tmp_path)
    assert "StartupBenchmark.kt" not in _of(report, DeadCodeKind.UNREACHABLE_FILE)
    assert "StartupBenchmark" not in _of(report, DeadCodeKind.UNUSED_EXPORT)


def test_zombie_packages(tmp_path: Path):
    zombies = _of(_report(tmp_path), DeadCodeKind.ZOMBIE_PACKAGE)
    assert zombies.isdisjoint({"catalog", "legacy", "benchmarks", "jwt-auth-tests"})
    assert "orphan" in zombies
