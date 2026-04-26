"""Unit tests for the Rust workspace-aware import resolver."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.rust import resolve_rust_import
from repowise.core.ingestion.resolvers.rust_workspace import (
    get_or_build_cargo_workspace_index,
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


def _write_workspace(repo: Path, members: list[str]) -> None:
    members_str = ", ".join(f'"{m}"' for m in members)
    (repo / "Cargo.toml").write_text(
        f"[workspace]\nmembers = [{members_str}]\n"
    )


def _write_member_crate(repo: Path, member_dir: str, name: str) -> None:
    crate_dir = repo / member_dir
    crate_dir.mkdir(parents=True, exist_ok=True)
    (crate_dir / "Cargo.toml").write_text(
        f"[package]\nname = \"{name}\"\nversion = \"0.1.0\"\n"
    )
    src_dir = crate_dir / "src"
    src_dir.mkdir(exist_ok=True)
    (src_dir / "lib.rs").write_text("// crate root\n")


class TestCargoWorkspaceIndex:
    def test_member_lookup(self, tmp_path: Path) -> None:
        _write_workspace(tmp_path, ["crates/foo", "crates/bar"])
        _write_member_crate(tmp_path, "crates/foo", "foo")
        _write_member_crate(tmp_path, "crates/bar", "bar-utils")
        ctx = _ctx(tmp_path, [
            "crates/foo/src/lib.rs",
            "crates/bar/src/lib.rs",
        ])
        idx = get_or_build_cargo_workspace_index(ctx)
        assert idx is not None
        assert idx.lookup("foo") == "crates/foo/src"
        # Hyphen → underscore in import identifier
        assert idx.lookup("bar_utils") == "crates/bar/src"
        # Bare hyphenated name should not match
        assert idx.lookup("bar-utils") is None

    def test_no_workspace_returns_none(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, [])
        assert get_or_build_cargo_workspace_index(ctx) is None


class TestRustWorkspaceResolution:
    def test_use_sibling_crate_resolves_to_module_file(self, tmp_path: Path) -> None:
        _write_workspace(tmp_path, ["crates/foo", "crates/bar"])
        _write_member_crate(tmp_path, "crates/foo", "foo")
        _write_member_crate(tmp_path, "crates/bar", "bar")
        # Add a module under bar to import
        (tmp_path / "crates/bar/src/baz.rs").write_text("pub fn hello(){}")

        ctx = _ctx(tmp_path, [
            "crates/foo/src/lib.rs",
            "crates/foo/src/main.rs",
            "crates/bar/src/lib.rs",
            "crates/bar/src/baz.rs",
        ])
        ctx.parsed_files = {p: None for p in ctx.path_set}
        result = resolve_rust_import("bar::baz", "crates/foo/src/main.rs", ctx)
        assert result == "crates/bar/src/baz.rs"

    def test_use_sibling_crate_root_only(self, tmp_path: Path) -> None:
        _write_workspace(tmp_path, ["crates/foo", "crates/bar"])
        _write_member_crate(tmp_path, "crates/foo", "foo")
        _write_member_crate(tmp_path, "crates/bar", "bar")

        ctx = _ctx(tmp_path, [
            "crates/foo/src/lib.rs",
            "crates/foo/src/main.rs",
            "crates/bar/src/lib.rs",
        ])
        ctx.parsed_files = {p: None for p in ctx.path_set}
        result = resolve_rust_import("bar::SomeType", "crates/foo/src/main.rs", ctx)
        assert result == "crates/bar/src/lib.rs"

    def test_unknown_crate_falls_through_to_external(self, tmp_path: Path) -> None:
        _write_workspace(tmp_path, ["crates/foo"])
        _write_member_crate(tmp_path, "crates/foo", "foo")

        ctx = _ctx(tmp_path, ["crates/foo/src/lib.rs", "crates/foo/src/main.rs"])
        ctx.parsed_files = {p: None for p in ctx.path_set}
        result = resolve_rust_import("serde::Serialize", "crates/foo/src/main.rs", ctx)
        assert result is not None and result.startswith("external:")

    def test_no_workspace_unaffected(self, tmp_path: Path) -> None:
        # Single-crate Cargo.toml without [workspace] — existing path still wins
        ctx = _ctx(tmp_path, ["src/lib.rs", "src/main.rs"])
        ctx.parsed_files = {p: None for p in ctx.path_set}
        result = resolve_rust_import("serde::Serialize", "src/main.rs", ctx)
        assert result is not None and result.startswith("external:")
