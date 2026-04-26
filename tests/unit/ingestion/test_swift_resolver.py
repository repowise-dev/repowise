"""Unit tests for the Swift Package.swift-aware import resolver."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.swift import resolve_swift_import
from repowise.core.ingestion.resolvers.swift_spm import (
    build_swift_targets,
    parse_package_swift,
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


_PACKAGE_SWIFT = """
// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "MyPkg",
    targets: [
        .target(name: "Core"),
        .target(name: "Helpers", path: "Custom/Helpers"),
        .executableTarget(name: "App"),
        .testTarget(name: "CoreTests"),
    ]
)
"""


class TestPackageSwiftParsing:
    def test_default_sources_path(self, tmp_path: Path) -> None:
        pkg = tmp_path / "Package.swift"
        pkg.write_text(_PACKAGE_SWIFT)
        targets = parse_package_swift(pkg)
        assert targets["Core"] == "Sources/Core"
        assert targets["App"] == "Sources/App"

    def test_explicit_path(self, tmp_path: Path) -> None:
        pkg = tmp_path / "Package.swift"
        pkg.write_text(_PACKAGE_SWIFT)
        targets = parse_package_swift(pkg)
        assert targets["Helpers"] == "Custom/Helpers"

    def test_test_target_uses_tests_root(self, tmp_path: Path) -> None:
        pkg = tmp_path / "Package.swift"
        pkg.write_text(_PACKAGE_SWIFT)
        targets = parse_package_swift(pkg)
        assert targets["CoreTests"] == "Tests/CoreTests"

    def test_missing_file(self, tmp_path: Path) -> None:
        assert parse_package_swift(tmp_path / "missing.swift") == {}


class TestSwiftResolverIntegration:
    def test_resolves_via_target_dir(self, tmp_path: Path) -> None:
        (tmp_path / "Package.swift").write_text(_PACKAGE_SWIFT)
        ctx = _ctx(
            tmp_path,
            ["Sources/Core/Engine.swift", "Sources/App/main.swift"],
        )
        # Trigger build_swift_targets via the lazy accessor in swift.py.
        result = resolve_swift_import("Core.Engine", "Sources/App/main.swift", ctx)
        assert result == "Sources/Core/Engine.swift"

    def test_falls_through_when_no_package_swift(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["Sources/Foo/Foo.swift"])
        # No Package.swift → swift_targets empty → falls through to stem.
        result = resolve_swift_import("Foo", "src/main.swift", ctx)
        assert result == "Sources/Foo/Foo.swift"


class TestBuildSwiftTargets:
    def test_merges_multiple_packages(self, tmp_path: Path) -> None:
        (tmp_path / "pkg-a").mkdir()
        (tmp_path / "pkg-a" / "Package.swift").write_text(
            'let package = Package(name: "A", targets: [.target(name: "Alpha")])'
        )
        (tmp_path / "pkg-b").mkdir()
        (tmp_path / "pkg-b" / "Package.swift").write_text(
            'let package = Package(name: "B", targets: [.target(name: "Beta")])'
        )
        merged = build_swift_targets(tmp_path)
        assert merged["Alpha"] == "pkg-a/Sources/Alpha"
        assert merged["Beta"] == "pkg-b/Sources/Beta"
