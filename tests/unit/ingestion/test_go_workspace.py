"""Unit tests for the Go package index (GoPackageIndex)."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.go_workspace import (
    build_go_package_index,
    get_or_build_go_index,
)


def _ctx(repo: Path, paths: list[str], go_modules: tuple = ()) -> ResolverContext:
    return ResolverContext(
        path_set=set(paths),
        stem_map={},
        graph=nx.DiGraph(),
        repo_path=repo,
        go_modules=go_modules,
    )


def _write(repo: Path, rel: str, body: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


class TestBuildIndex:
    def test_groups_sibling_files_into_one_package(self, tmp_path: Path) -> None:
        files = [
            "cache/filecache/filecache.go",
            "cache/filecache/filecache_config.go",
            "cache/filecache/filecache_pruner.go",
        ]
        for f in files:
            _write(tmp_path, f, "package filecache\n")
        ctx = _ctx(
            tmp_path, files, go_modules=(("", "github.com/gohugoio/hugo"),)
        )
        index = build_go_package_index(ctx)
        pkg = index.packages["cache/filecache"]
        assert pkg.pkg_name == "filecache"
        assert pkg.is_main is False
        assert pkg.files == tuple(sorted(files))

    def test_files_for_import_returns_all_package_files(self, tmp_path: Path) -> None:
        files = [
            "cache/filecache/filecache.go",
            "cache/filecache/filecache_config.go",
            "cache/filecache/filecache_pruner.go",
        ]
        for f in files:
            _write(tmp_path, f, "package filecache\n")
        ctx = _ctx(tmp_path, files, go_modules=(("", "github.com/gohugoio/hugo"),))
        index = build_go_package_index(ctx)
        resolved = index.files_for_import("github.com/gohugoio/hugo/cache/filecache")
        assert set(resolved) == set(files)

    def test_external_import_returns_empty(self, tmp_path: Path) -> None:
        _write(tmp_path, "main.go", "package main\n")
        ctx = _ctx(tmp_path, ["main.go"], go_modules=(("", "github.com/me/app"),))
        index = build_go_package_index(ctx)
        assert index.files_for_import("github.com/spf13/cobra") == ()

    def test_is_main_detection(self, tmp_path: Path) -> None:
        _write(tmp_path, "cmd/app/main.go", "package main\n\nfunc main() {}\n")
        _write(tmp_path, "pkg/lib/lib.go", "package lib\n")
        files = ["cmd/app/main.go", "pkg/lib/lib.go"]
        ctx = _ctx(tmp_path, files, go_modules=(("", "github.com/me/app"),))
        index = build_go_package_index(ctx)
        assert index.packages["cmd/app"].is_main is True
        assert index.packages["pkg/lib"].is_main is False

    def test_init_and_build_tag_detection(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "pkg/feat/feat.go",
            "//go:build linux\n\npackage feat\n\nfunc init() {}\n",
        )
        ctx = _ctx(tmp_path, ["pkg/feat/feat.go"], go_modules=(("", "m"),))
        index = build_go_package_index(ctx)
        pkg = index.packages["pkg/feat"]
        assert pkg.has_init is True
        assert pkg.build_constrained is True

    def test_monorepo_longest_module_prefix(self, tmp_path: Path) -> None:
        files = ["services/foo/handler.go", "libs/bar/util.go"]
        for f in files:
            _write(tmp_path, f, "package p\n")
        modules = (
            ("services/foo", "github.com/me/foo"),
            ("", "github.com/me/root"),
        )
        ctx = _ctx(tmp_path, files, go_modules=modules)
        index = build_go_package_index(ctx)
        # The nested module's package gets the nested module's import path,
        # not root/services/foo.
        assert (
            index.files_for_import("github.com/me/foo")
            == ("services/foo/handler.go",)
        )
        # A package under the root module resolves via the root prefix.
        assert (
            index.files_for_import("github.com/me/root/libs/bar")
            == ("libs/bar/util.go",)
        )

    def test_package_for_file(self, tmp_path: Path) -> None:
        files = ["pkg/lib/a.go", "pkg/lib/b.go"]
        for f in files:
            _write(tmp_path, f, "package lib\n")
        ctx = _ctx(tmp_path, files, go_modules=(("", "m"),))
        index = build_go_package_index(ctx)
        pkg = index.package_for_file("pkg/lib/a.go")
        assert pkg is not None and pkg.dir == "pkg/lib"

    def test_memoised_accessor_caches(self, tmp_path: Path) -> None:
        _write(tmp_path, "main.go", "package main\n")
        ctx = _ctx(tmp_path, ["main.go"], go_modules=(("", "m"),))
        first = get_or_build_go_index(ctx)
        second = get_or_build_go_index(ctx)
        assert first is second


class TestPackageMainEntries:
    def test_main_files_recorded_regardless_of_filename(self, tmp_path: Path) -> None:
        # Go's entry convention is semantic (package main + func main), not
        # filename-based — cmd/task/task.go is as much a binary as
        # cmd/release/main.go.
        _write(tmp_path, "cmd/task/task.go", "package main\n\nfunc main() {}\n")
        _write(tmp_path, "cmd/release/main.go", "package main\n\nfunc main() {}\n")
        # package main helper WITHOUT func main: never an entry.
        _write(tmp_path, "cmd/task/helpers.go", "package main\n\nfunc run() {}\n")
        # Library package with a func main-looking method: never an entry.
        _write(tmp_path, "lib/lib.go", "package lib\n\nfunc main() {}\n")
        paths = ["cmd/task/task.go", "cmd/release/main.go", "cmd/task/helpers.go", "lib/lib.go"]
        ctx = _ctx(tmp_path, paths, go_modules=(("", "m"),))
        index = build_go_package_index(ctx)
        assert index.packages["cmd/task"].main_files == ("cmd/task/task.go",)
        assert index.packages["cmd/release"].main_files == ("cmd/release/main.go",)
        assert index.packages["lib"].main_files == ()

    def test_warmup_stamps_entry_flag_on_main_files(self, tmp_path: Path) -> None:
        from repowise.core.ingestion.graph_warmups import _warmup_go

        _write(tmp_path, "cmd/task/task.go", "package main\n\nfunc main() {}\n")
        _write(tmp_path, "lib/lib.go", "package lib\n")
        paths = ["cmd/task/task.go", "lib/lib.go"]
        ctx = _ctx(tmp_path, paths, go_modules=(("", "m"),))
        for p in paths:
            ctx.graph.add_node(p, node_type="file")
        _warmup_go(ctx)
        assert ctx.graph.nodes["cmd/task/task.go"].get("is_entry_point") is True
        assert "is_entry_point" not in ctx.graph.nodes["lib/lib.go"]


def test_warmup_stamps_parsed_file_info(tmp_path: Path) -> None:
    # The exported KG's entry tags read FileInfo.is_entry_point, not the
    # graph attr — the warmup must stamp both surfaces.
    from types import SimpleNamespace

    from repowise.core.ingestion.graph_warmups import _warmup_go

    _write(tmp_path, "cmd/task/task.go", "package main\n\nfunc main() {}\n")
    ctx = _ctx(tmp_path, ["cmd/task/task.go"], go_modules=(("", "m"),))
    fi = SimpleNamespace(is_entry_point=False)
    ctx.parsed_files = {"cmd/task/task.go": SimpleNamespace(file_info=fi)}
    ctx.graph.add_node("cmd/task/task.go", node_type="file")
    _warmup_go(ctx)
    assert fi.is_entry_point is True
