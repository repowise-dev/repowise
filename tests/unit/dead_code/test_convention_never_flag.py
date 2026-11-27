"""Tool-loaded files that no import reaches are never reported unreachable.

Each convention has positives (loaded by a tool or runtime by name) and near
misses that must stay flaggable.
"""

from __future__ import annotations

from types import SimpleNamespace

import networkx as nx
import pytest

from repowise.core.analysis.dead_code.constants import (
    is_runner_file,
    never_flag_match,
    never_flag_path,
)
from repowise.core.analysis.dead_code.file_reachability import (
    ReachabilityRescues,
    is_file_reachable,
)


def _unreachable(path: str) -> bool:
    graph = nx.DiGraph()
    graph.add_node(path, language="typescript")
    return not is_file_reachable(path, graph)


@pytest.mark.parametrize(
    "path",
    [
        # Any tool's ``<name>.config.<js-family ext>`` is loaded by that tool.
        "webpack.config.js",
        "apps/docs/astro.config.mjs",
        "packages/ui/tsup.config.ts",
        "svelte.config.cjs",
        "packages/app/vite.config.mts",
        "drizzle.config.ts",
        # ``.<tool>rc.<js-family ext>``.
        ".eslintrc.js",
        "packages/web/.eslintrc.cjs",
        ".prettierrc.mjs",
        ".mocharc.ts",
        # pnpm's install hook.
        ".pnpmfile.cjs",
        "tools/.pnpmfile.cjs",
        # Next.js request proxy, the successor of ``middleware.ts``.
        "proxy.ts",
        "apps/web/proxy.ts",
        "apps/web/src/proxy.js",
    ],
)
def test_js_tool_convention_files_are_never_flagged(path):
    assert never_flag_match(path)
    assert not _unreachable(path)


@pytest.mark.parametrize(
    "path",
    [
        # Astro pages and endpoints: routed by their path under src/pages, never imported.
        "src/pages/index.astro",
        "apps/web/src/pages/blog/[slug].astro",
        "src/pages/robots.txt.ts",
        "apps/site/src/pages/api/products/[id].json.ts",
        "src/pages/rss.xml.js",
        "src/pages/feed.json.mjs",
    ],
)
def test_astro_routes_are_never_flagged(path):
    assert never_flag_match(path)
    assert not _unreachable(path)


@pytest.mark.parametrize(
    "path",
    [
        # CPython imports these at startup when they are on sys.path.
        "sitecustomize.py",
        "src/sitecustomize.py",
        "usercustomize.py",
        "tools/usercustomize.py",
    ],
)
def test_python_startup_hooks_are_never_flagged(path):
    assert never_flag_match(path)


@pytest.mark.parametrize(
    "path",
    [
        "cleanup.rake",
        "lib/tasks/cleanup.rake",
        "show.json.jbuilder",
        "app/views/api/show.json.jbuilder",
        "Capfile",
        "config/Capfile",
        "Guardfile",
        "config/Guardfile",
        "Vagrantfile",
        "config/Vagrantfile",
    ],
)
def test_ruby_tool_convention_files_are_never_flagged(path):
    assert never_flag_match(path)
    assert not _unreachable(path)


@pytest.mark.parametrize(
    "path",
    [
        "src/config.ts",  # no tool name before ``.config``
        "src/settings.config.py",  # not a JS-family config
        "src/myconfig.js",
        "src/eslintrc.js",  # not a dotfile
        "src/source.js",  # ends in ``rc.js`` but is no rc file
        "src/api/reverse-proxy.ts",
        "src/httpproxy.ts",
        "src/mysitecustomize.py",
        "src/sitecustomize_helpers.py",
        "src/lib/pages/format.ts",  # a pages dir outside src/pages routes nothing
        "src/components/Header.astro",
        "lib/Capfile.rb",
        "lib/Guardfile.rb",
        "lib/Vagrantfile.rb",
    ],
)
def test_near_misses_stay_flaggable(path):
    assert not never_flag_match(path)


@pytest.mark.parametrize(
    "path",
    [
        # Copied libraries and generator output are not this repository's to delete.
        "src/native/external/zlib/trees.c",
        "x-pack/plugin/esql/src/main/generated/org/x/FooEvaluator.java",
    ],
)
def test_vendored_and_generated_files_are_never_flagged(path):
    assert never_flag_path(path)
    assert not _unreachable(path)


@pytest.mark.parametrize(
    "path",
    [
        "src/pkg/external/client.py",
        "x-pack/plugin/inference/src/main/java/org/x/external/Sender.java",
        "src/libraries/System.Runtime/src/System/DateTime.cs",
        "Gemfile.rb",
        "lib/Gemfile.rb",
    ],
)
def test_origin_near_misses_stay_flaggable(path):
    assert not never_flag_path(path)


@pytest.mark.parametrize("path", ["Gemfile", "apps/api/Gemfile"])
def test_package_manifests_are_not_unreachable(path):
    from repowise.core.analysis.dead_code import DeadCodeAnalyzer

    graph = nx.DiGraph()
    graph.add_node(path, node_type="file", language="ruby")
    assert not never_flag_path(path)
    assert not is_file_reachable(path, graph)
    assert not DeadCodeAnalyzer(graph, {})._detect_unreachable_files(set())


def test_reachable_gemfile_still_reports_unused_symbols(tmp_path):
    from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
    from repowise.core.generation.context.assembler import _flow_entry_is_reachable
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    source = b"def unused_helper\n  1\nend\n"
    (tmp_path / "Gemfile").write_bytes(source)
    file_info = next(FileTraverser(tmp_path).traverse())
    parsed = ASTParser().parse_file(file_info, source)
    builder = GraphBuilder(repo_path=tmp_path)
    builder.add_file(parsed)
    builder.set_source_map({"Gemfile": source})
    graph = builder.build()
    report = DeadCodeAnalyzer(
        graph,
        {},
        parsed_files={"Gemfile": parsed},
        source_map={"Gemfile": source},
        repo_root=tmp_path,
    ).analyze({"min_confidence": 0.0})

    assert not any(f.kind is DeadCodeKind.UNREACHABLE_FILE for f in report.findings)
    assert any(
        f.kind is DeadCodeKind.UNUSED_EXPORT and f.symbol_name == "unused_helper"
        for f in report.findings
    )
    helper_id = next(n for n, data in graph.nodes(data=True) if data.get("name") == "unused_helper")
    flow = SimpleNamespace(entry_point_id=helper_id)
    assert not _flow_entry_is_reachable(flow, graph, ReachabilityRescues())


@pytest.mark.parametrize(
    "path",
    [
        # A build tool runs a build file by its name; nothing imports one.
        "ktor-server/ktor-server-core/build.gradle.kts",
        "gradle/plugins/ktorbuild.kmp.gradle.kts",
        "settings.gradle.kts",
        "crates/core/build.rs",
        "src/bootstrap/build.rs",
        "src/tools/x/build.rs",
        "magefile.go",
        "noxfile.py",
        "cmake/Toolchain.cmake",
        "Package@swift-6.0.swift",
        "Package@swift-5.10.swift",
    ],
)
def test_build_files_are_never_flagged(path):
    assert never_flag_path(path)
    assert not _unreachable(path)


@pytest.mark.parametrize(
    "path",
    [
        "src/build.rs",  # a ``mod build;`` module
        "src/main/kotlin/io/ktor/Settings.kt",
        "lib/webpack.js",
        "PackageManager.swift",
        "Package+Extensions.swift",
    ],
)
def test_build_near_misses_stay_flaggable(path):
    assert not never_flag_path(path)


def test_a_build_file_runs_what_it_names():
    assert is_runner_file("CMakeLists.txt")
    assert is_runner_file("server/build.gradle")
    assert is_runner_file("eng/Versions.props")
    assert not is_runner_file("src/main.c")


@pytest.mark.parametrize(
    "path",
    [
        # build_runner outputs, generated by name in ``code_origin``.
        "lib/src/photo.g.dart",
        "lib/models/user.freezed.dart",
        "lib/router/app_router.gr.dart",
        "test/api_test.mocks.dart",
        "lib/gen/assets.gen.dart",
        # Injectable's DI config, kept as a dead-code glob only.
        "lib/di/injection.config.dart",
    ],
)
def test_dart_codegen_outputs_are_never_flagged(path):
    assert never_flag_path(path)
    assert not _unreachable(path)


def test_hand_written_dart_stays_flaggable():
    assert not never_flag_path("lib/src/photo.dart")


def test_swiftpm_version_specific_manifests_not_reported_unreachable(tmp_path):
    from pathlib import Path

    from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    manifest_59 = (
        "// swift-tools-version:5.9\n"
        "import PackageDescription\n"
        'let package = Package(name: "Alamofire", targets: [.target(name: "Alamofire")])\n'
    )
    files = {
        "Package.swift": manifest_59,
        "Package@swift-6.0.swift": manifest_59.replace("5.9", "6.0"),
        "Package@swift-5.10.swift": manifest_59.replace("5.9", "5.10"),
        "Sources/Alamofire/Session.swift": "public final class Session {}\n",
    }
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    parser = ASTParser()
    builder = GraphBuilder(repo_path=tmp_path)
    source_map: dict[str, bytes] = {}
    parsed_files = {}
    for fi in FileTraverser(tmp_path).traverse():
        source = Path(fi.abs_path).read_bytes()
        source_map[fi.path] = source
        parsed = parser.parse_file(fi, source)
        parsed_files[fi.path] = parsed
        builder.add_file(parsed)
    builder.set_source_map(source_map)
    graph = builder.build()

    report = DeadCodeAnalyzer(
        graph,
        {},
        parsed_files=parsed_files,
        source_map=source_map,
        repo_root=tmp_path,
    ).analyze(None)
    unreachable = {
        f.file_path for f in report.findings if f.kind is DeadCodeKind.UNREACHABLE_FILE
    }
    assert "Package.swift" not in unreachable
    assert "Package@swift-6.0.swift" not in unreachable
    assert "Package@swift-5.10.swift" not in unreachable

