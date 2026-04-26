"""Unit tests for the Go multi-module resolver."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.go import (
    read_go_modules,
    resolve_go_import,
)


def _ctx(repo: Path, paths: list[str], go_modules: tuple = ()) -> ResolverContext:
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
        go_modules=go_modules,
    )


class TestReadGoModules:
    def test_finds_root_and_nested_modules(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module github.com/me/root\n")
        (tmp_path / "services" / "foo").mkdir(parents=True)
        (tmp_path / "services" / "foo" / "go.mod").write_text(
            "module github.com/me/foo\n"
        )
        modules = read_go_modules(tmp_path)
        # Longest first: foo's path is shorter than root's? Both equal length
        # — but ``github.com/me/foo`` (19) vs ``github.com/me/root`` (20).
        # We just assert both modules are present.
        names = [m for _, m in modules]
        assert "github.com/me/root" in names
        assert "github.com/me/foo" in names

    def test_skips_vendor(self, tmp_path: Path) -> None:
        (tmp_path / "vendor" / "x").mkdir(parents=True)
        (tmp_path / "vendor" / "x" / "go.mod").write_text("module vendored\n")
        assert read_go_modules(tmp_path) == ()


class TestResolveGoImport:
    def test_multi_module_picks_nearest_module(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            ["services/foo/handler.go", "libs/bar/util.go"],
            go_modules=(
                ("services/foo", "github.com/me/foo"),
                ("libs/bar", "github.com/me/bar"),
            ),
        )
        # Sort longest-first as the real builder does.
        ctx.go_modules = tuple(sorted(ctx.go_modules, key=lambda t: len(t[1]), reverse=True))
        result = resolve_go_import("github.com/me/bar", "services/foo/handler.go", ctx)
        assert result == "libs/bar/util.go"

    def test_single_module_back_compat(self, tmp_path: Path) -> None:
        # Build via legacy ``go_module_path`` only (no go_modules tuple).
        path_set = {"pkg/util.go"}
        stem_map = {"util": ["pkg/util.go"]}
        ctx = ResolverContext(
            path_set=path_set,
            stem_map=stem_map,
            graph=nx.DiGraph(),
            repo_path=tmp_path,
            go_module_path="github.com/me/root",
        )
        result = resolve_go_import("github.com/me/root/pkg", "main.go", ctx)
        assert result == "pkg/util.go"

    def test_external_falls_back(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["pkg/util.go"])
        result = resolve_go_import("github.com/external/lib", "main.go", ctx)
        assert result == "external:github.com/external/lib"
