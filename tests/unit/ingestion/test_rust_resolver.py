"""Unit tests for the Rust workspace-aware import resolver."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.rust import resolve_rust_import
from repowise.core.ingestion.resolvers.rust_workspace import (
    CargoCrate,
    CargoDep,
    CargoWorkspaceIndex,
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


class TestCargoWorkspaceGlobExpansion:
    def test_glob_member_pattern(self, tmp_path: Path) -> None:
        """Glob pattern members = ["crates/*"] should discover all crate directories."""
        _write_workspace(tmp_path, ["crates/*"])
        _write_member_crate(tmp_path, "crates/foo", "foo")
        _write_member_crate(tmp_path, "crates/bar", "bar-utils")
        ctx = _ctx(tmp_path, [
            "crates/foo/src/lib.rs",
            "crates/bar/src/lib.rs",
        ])
        idx = get_or_build_cargo_workspace_index(ctx)
        assert idx is not None
        assert idx.lookup("foo") == "crates/foo/src"
        assert idx.lookup("bar_utils") == "crates/bar/src"

    def test_exclude_pattern(self, tmp_path: Path) -> None:
        """Workspace exclude patterns should skip matching crates."""
        (tmp_path / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["crates/*"]\nexclude = ["crates/ignored"]\n'
        )
        _write_member_crate(tmp_path, "crates/foo", "foo")
        _write_member_crate(tmp_path, "crates/ignored", "ignored")
        ctx = _ctx(tmp_path, [
            "crates/foo/src/lib.rs",
            "crates/ignored/src/lib.rs",
        ])
        idx = get_or_build_cargo_workspace_index(ctx)
        assert idx is not None
        assert idx.lookup("foo") == "crates/foo/src"
        assert idx.lookup("ignored") is None

    def test_root_member_dot(self, tmp_path: Path) -> None:
        """members = ["."] should index the root crate without crashing.

        Python 3.14+ rejects ``pathlib.Path.glob(".")`` with
        ``ValueError: Unacceptable pattern``. Cargo workspaces commonly
        declare ``"."`` as a member to include the root crate.
        """
        (tmp_path / "Cargo.toml").write_text(
            '[workspace]\nmembers = [".", "crates/foo"]\n'
            '[package]\nname = "root_crate"\nversion = "0.1.0"\n'
        )
        _write_member_crate(tmp_path, "crates/foo", "foo")
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / "lib.rs").write_text("// root crate\n")
        ctx = _ctx(tmp_path, [
            "src/lib.rs",
            "crates/foo/src/lib.rs",
        ])
        idx = get_or_build_cargo_workspace_index(ctx)
        assert idx is not None
        assert idx.lookup("root_crate") == "src"
        assert idx.lookup("foo") == "crates/foo/src"

    def test_exclude_dot(self, tmp_path: Path) -> None:
        """exclude = ["."] should not crash (root pkg still indexed via [package])."""
        (tmp_path / "Cargo.toml").write_text(
            '[workspace]\nmembers = [".", "crates/foo"]\nexclude = ["."]\n'
            '[package]\nname = "root_crate"\nversion = "0.1.0"\n'
        )
        _write_member_crate(tmp_path, "crates/foo", "foo")
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / "lib.rs").write_text("// root crate\n")
        ctx = _ctx(tmp_path, [
            "src/lib.rs",
            "crates/foo/src/lib.rs",
        ])
        idx = get_or_build_cargo_workspace_index(ctx)
        assert idx is not None
        assert idx.lookup("foo") == "crates/foo/src"


def test_rust_visibility_levels():
    from repowise.core.ingestion.extractors.visibility import rust_visibility
    assert rust_visibility("foo", ["pub"]) == "public"
    assert rust_visibility("foo", ["pub(crate)"]) == "internal"
    assert rust_visibility("foo", ["pub(super)"]) == "protected"
    assert rust_visibility("foo", ["pub(in crate::module)"]) == "protected"
    assert rust_visibility("foo", []) == "private"


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


class TestAliasedAndBraceImports:
    def test_aliased_import_strips_as_suffix(self, tmp_path: Path) -> None:
        """``use typst_syntax as syntax`` should resolve the same as ``typst_syntax``."""
        _write_workspace(tmp_path, ["crates/typst", "crates/typst-syntax"])
        _write_member_crate(tmp_path, "crates/typst", "typst")
        _write_member_crate(tmp_path, "crates/typst-syntax", "typst-syntax")

        ctx = _ctx(tmp_path, [
            "crates/typst/src/lib.rs",
            "crates/typst-syntax/src/lib.rs",
        ])
        ctx.parsed_files = {p: None for p in ctx.path_set}

        # Aliased form should resolve to the same target as the bare form
        aliased = resolve_rust_import(
            "typst_syntax as syntax", "crates/typst/src/lib.rs", ctx
        )
        bare = resolve_rust_import(
            "typst_syntax", "crates/typst/src/lib.rs", ctx
        )
        assert aliased == bare
        assert aliased == "crates/typst-syntax/src/lib.rs"

    def test_brace_import_resolves_base_module(self, tmp_path: Path) -> None:
        """``use crate::diag::{A, B}`` should resolve to ``diag.rs``."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "lib.rs").write_text("// root\n")
        (tmp_path / "src" / "diag.rs").write_text("pub struct A; pub struct B;\n")

        ctx = _ctx(tmp_path, ["src/lib.rs", "src/diag.rs"])
        ctx.parsed_files = {p: None for p in ctx.path_set}

        result = resolve_rust_import("crate::diag::{A, B}", "src/lib.rs", ctx)
        assert result == "src/diag.rs"

    def test_brace_only_import_returns_none(self, tmp_path: Path) -> None:
        """``{A, B}`` (no base path) should return None gracefully without crashing."""
        ctx = _ctx(tmp_path, ["src/lib.rs"])
        ctx.parsed_files = {p: None for p in ctx.path_set}

        result = resolve_rust_import("{A, B}", "src/lib.rs", ctx)
        # After stripping the brace segment all parts are gone — None is correct.
        assert result is None


class TestSuperChainedResolution:
    def test_single_super(self, tmp_path: Path) -> None:
        (tmp_path / "a").mkdir(parents=True)
        (tmp_path / "a/b").mkdir(parents=True)
        (tmp_path / "a/foo.rs").write_text("pub fn hello(){}")
        ctx = _ctx(tmp_path, ["a/foo.rs", "a/b/bar.rs"])
        ctx.parsed_files = {p: None for p in ctx.path_set}
        result = resolve_rust_import("super::foo", "a/b/bar.rs", ctx)
        assert result == "a/foo.rs"

    def test_double_super(self, tmp_path: Path) -> None:
        (tmp_path / "a/b").mkdir(parents=True)
        (tmp_path / "foo.rs").write_text("pub fn hello(){}")
        ctx = _ctx(tmp_path, ["foo.rs", "a/b/deep.rs"])
        ctx.parsed_files = {p: None for p in ctx.path_set}
        result = resolve_rust_import("super::super::foo", "a/b/deep.rs", ctx)
        assert result == "foo.rs"

    def test_bare_super_returns_none(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["a/b/bar.rs"])
        ctx.parsed_files = {p: None for p in ctx.path_set}
        result = resolve_rust_import("super::super", "a/b/bar.rs", ctx)
        assert result is None


class TestCargoDependencyParsing:
    def test_dependencies_parsed(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["crates/foo"]\n'
        )
        crate_dir = tmp_path / "crates" / "foo"
        crate_dir.mkdir(parents=True)
        (crate_dir / "Cargo.toml").write_text(
            '[package]\nname = "foo"\nversion = "0.1.0"\n\n'
            '[dependencies]\nserde = "1.0"\n\n'
            '[dependencies.bar]\npath = "../bar"\npackage = "bar-impl"\n'
        )
        (crate_dir / "src").mkdir()
        (crate_dir / "src" / "lib.rs").write_text("// root\n")
        ctx = _ctx(tmp_path, ["crates/foo/src/lib.rs"])
        idx = get_or_build_cargo_workspace_index(ctx)
        assert idx is not None
        # Find the "foo" crate
        foo = next(c for c in idx.crates if c.name == "foo")
        assert any(d.name == "serde" and not d.is_path for d in foo.dependencies)
        assert any(
            d.name == "bar" and d.is_path and d.package == "bar-impl"
            for d in foo.dependencies
        )

    def test_dev_dependencies_parsed(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["crates/foo"]\n'
        )
        crate_dir = tmp_path / "crates" / "foo"
        crate_dir.mkdir(parents=True)
        (crate_dir / "Cargo.toml").write_text(
            '[package]\nname = "foo"\nversion = "0.1.0"\n\n'
            '[dev-dependencies]\ntokio = "1.0"\n'
        )
        (crate_dir / "src").mkdir()
        (crate_dir / "src" / "lib.rs").write_text("// root\n")
        ctx = _ctx(tmp_path, ["crates/foo/src/lib.rs"])
        idx = get_or_build_cargo_workspace_index(ctx)
        assert idx is not None
        foo = next(c for c in idx.crates if c.name == "foo")
        assert any(d.name == "tokio" for d in foo.dependencies)

    def test_workspace_dependencies(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text(
            '[workspace]\nmembers = []\n\n'
            '[workspace.dependencies]\nserde = "1.0"\n'
        )
        ctx = _ctx(tmp_path, [])
        idx = get_or_build_cargo_workspace_index(ctx)
        # No crates means None is returned (empty workspace)
        # We need at least one crate to get a non-None result
        assert idx is None

    def test_workspace_dependencies_with_member(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["crates/foo"]\n\n'
            '[workspace.dependencies]\nserde = "1.0"\n'
        )
        crate_dir = tmp_path / "crates" / "foo"
        crate_dir.mkdir(parents=True)
        (crate_dir / "Cargo.toml").write_text(
            '[package]\nname = "foo"\nversion = "0.1.0"\n'
        )
        (crate_dir / "src").mkdir()
        (crate_dir / "src" / "lib.rs").write_text("// root\n")
        ctx = _ctx(tmp_path, ["crates/foo/src/lib.rs"])
        idx = get_or_build_cargo_workspace_index(ctx)
        assert idx is not None
        assert any(d.name == "serde" for d in idx.workspace_dependencies)


class TestFileToCrateMapping:
    def test_lookup_crate_for_file(self, tmp_path: Path) -> None:
        _write_workspace(tmp_path, ["crates/*"])
        _write_member_crate(tmp_path, "crates/foo", "foo")
        _write_member_crate(tmp_path, "crates/bar", "bar")
        ctx = _ctx(tmp_path, ["crates/foo/src/lib.rs", "crates/bar/src/lib.rs"])
        idx = get_or_build_cargo_workspace_index(ctx)
        assert idx is not None
        foo_crate = idx.lookup_crate_for_file("crates/foo/src/lib.rs")
        assert foo_crate is not None
        assert foo_crate.name == "foo"
        bar_crate = idx.lookup_crate_for_file("crates/bar/src/lib.rs")
        assert bar_crate is not None
        assert bar_crate.name == "bar"

    def test_lookup_nested_file(self, tmp_path: Path) -> None:
        _write_workspace(tmp_path, ["crates/*"])
        _write_member_crate(tmp_path, "crates/foo", "foo")
        ctx = _ctx(tmp_path, ["crates/foo/src/lib.rs", "crates/foo/src/utils/helper.rs"])
        idx = get_or_build_cargo_workspace_index(ctx)
        assert idx is not None
        crate = idx.lookup_crate_for_file("crates/foo/src/utils/helper.rs")
        assert crate is not None
        assert crate.name == "foo"

    def test_lookup_unknown_file(self, tmp_path: Path) -> None:
        _write_workspace(tmp_path, ["crates/*"])
        _write_member_crate(tmp_path, "crates/foo", "foo")
        ctx = _ctx(tmp_path, ["crates/foo/src/lib.rs"])
        idx = get_or_build_cargo_workspace_index(ctx)
        assert idx is not None
        assert idx.lookup_crate_for_file("unknown/path/file.rs") is None


class TestBareIdentifierProbesImporterDir:
    """Fix 2: single-segment bare identifiers (from ``mod foo;``) should probe
    the importer's directory before the crate root."""

    def test_mod_in_subdir_resolves_locally(self, tmp_path: Path) -> None:
        """``mod foo;`` in ``crates/typst/src/eval/mod.rs`` should find
        ``crates/typst/src/eval/foo.rs``, not ``crates/typst/src/foo.rs``."""
        (tmp_path / "crates/typst/src/eval").mkdir(parents=True)
        (tmp_path / "crates/typst/src/lib.rs").write_text("// root\n")
        (tmp_path / "crates/typst/src/eval/mod.rs").write_text("mod foo;\n")
        (tmp_path / "crates/typst/src/eval/foo.rs").write_text("pub fn run(){}\n")
        # Also create foo.rs at crate root so both candidates exist
        (tmp_path / "crates/typst/src/foo.rs").write_text("pub fn other(){}\n")

        ctx = _ctx(tmp_path, [
            "crates/typst/src/lib.rs",
            "crates/typst/src/eval/mod.rs",
            "crates/typst/src/eval/foo.rs",
            "crates/typst/src/foo.rs",
        ])
        ctx.parsed_files = {p: None for p in ctx.path_set}
        result = resolve_rust_import("foo", "crates/typst/src/eval/mod.rs", ctx)
        assert result == "crates/typst/src/eval/foo.rs"

    def test_mod_in_crate_root_still_works(self, tmp_path: Path) -> None:
        """``mod foo;`` in ``crates/typst/src/lib.rs`` should find
        ``crates/typst/src/foo.rs``."""
        (tmp_path / "crates/typst/src").mkdir(parents=True)
        (tmp_path / "crates/typst/src/lib.rs").write_text("mod foo;\n")
        (tmp_path / "crates/typst/src/foo.rs").write_text("pub fn hello(){}\n")

        ctx = _ctx(tmp_path, [
            "crates/typst/src/lib.rs",
            "crates/typst/src/foo.rs",
        ])
        ctx.parsed_files = {p: None for p in ctx.path_set}
        result = resolve_rust_import("foo", "crates/typst/src/lib.rs", ctx)
        assert result == "crates/typst/src/foo.rs"

    def test_mod_resolves_to_mod_rs_variant(self, tmp_path: Path) -> None:
        """``mod foo;`` should also match ``foo/mod.rs``."""
        (tmp_path / "src/foo").mkdir(parents=True)
        (tmp_path / "src/lib.rs").write_text("mod foo;\n")
        (tmp_path / "src/foo/mod.rs").write_text("pub fn hello(){}\n")

        ctx = _ctx(tmp_path, [
            "src/lib.rs",
            "src/foo/mod.rs",
        ])
        ctx.parsed_files = {p: None for p in ctx.path_set}
        result = resolve_rust_import("foo", "src/lib.rs", ctx)
        assert result == "src/foo/mod.rs"


class TestPathAttributeResolution:
    """Fix 3: ``#[path = "custom.rs"] mod foo;`` should resolve the path
    relative to the importer's directory."""

    def test_rs_suffix_resolves_relative_to_importer(self, tmp_path: Path) -> None:
        (tmp_path / "crates/foo/src").mkdir(parents=True)
        (tmp_path / "crates/foo/src/lib.rs").write_text("// root\n")
        (tmp_path / "crates/foo/src/custom.rs").write_text("pub fn hello(){}\n")

        ctx = _ctx(tmp_path, [
            "crates/foo/src/lib.rs",
            "crates/foo/src/custom.rs",
        ])
        ctx.parsed_files = {p: None for p in ctx.path_set}
        result = resolve_rust_import("custom.rs", "crates/foo/src/lib.rs", ctx)
        assert result == "crates/foo/src/custom.rs"

    def test_rs_suffix_nonexistent_returns_none(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["src/lib.rs"])
        ctx.parsed_files = {p: None for p in ctx.path_set}
        result = resolve_rust_import("nonexistent.rs", "src/lib.rs", ctx)
        assert result is None

    def test_rs_suffix_in_subdir(self, tmp_path: Path) -> None:
        """Path attribute from a file in a subdirectory."""
        (tmp_path / "src/sub").mkdir(parents=True)
        (tmp_path / "src/lib.rs").write_text("// root\n")
        (tmp_path / "src/sub/mod.rs").write_text("// mod\n")
        (tmp_path / "src/sub/impl_file.rs").write_text("pub fn run(){}\n")

        ctx = _ctx(tmp_path, [
            "src/lib.rs",
            "src/sub/mod.rs",
            "src/sub/impl_file.rs",
        ])
        ctx.parsed_files = {p: None for p in ctx.path_set}
        result = resolve_rust_import("impl_file.rs", "src/sub/mod.rs", ctx)
        assert result == "src/sub/impl_file.rs"


class TestTrailingUnderscoreFallback:
    """#[path]-renamed modules use trailing-underscore names (e.g. ``export_``)
    backed by files without the underscore (e.g. ``export.rs``)."""

    def test_self_import_with_trailing_underscore(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "lib.rs").write_text("// root\n")
        (tmp_path / "src" / "export.rs").write_text("pub struct BundleOptions;\n")

        ctx = _ctx(tmp_path, ["src/lib.rs", "src/export.rs"])
        ctx.parsed_files = {p: None for p in ctx.path_set}
        result = resolve_rust_import("self::export_", "src/lib.rs", ctx)
        assert result == "src/export.rs"

    def test_crate_import_with_trailing_underscore(self, tmp_path: Path) -> None:
        (tmp_path / "src/model").mkdir(parents=True)
        (tmp_path / "src" / "lib.rs").write_text("// root\n")
        (tmp_path / "src/model" / "enum.rs").write_text("pub enum MyEnum {}\n")

        ctx = _ctx(tmp_path, ["src/lib.rs", "src/model/enum.rs"])
        ctx.parsed_files = {p: None for p in ctx.path_set}
        result = resolve_rust_import("crate::model::enum_", "src/lib.rs", ctx)
        assert result == "src/model/enum.rs"

    def test_no_false_positive_on_regular_module(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "lib.rs").write_text("// root\n")
        (tmp_path / "src" / "my_module.rs").write_text("pub fn hello(){}\n")

        ctx = _ctx(tmp_path, ["src/lib.rs", "src/my_module.rs"])
        ctx.parsed_files = {p: None for p in ctx.path_set}
        result = resolve_rust_import("crate::my_module", "src/lib.rs", ctx)
        assert result == "src/my_module.rs"

    def test_trailing_underscore_with_mod_rs(self, tmp_path: Path) -> None:
        (tmp_path / "src/export").mkdir(parents=True)
        (tmp_path / "src" / "lib.rs").write_text("// root\n")
        (tmp_path / "src/export" / "mod.rs").write_text("pub fn run(){}\n")

        ctx = _ctx(tmp_path, ["src/lib.rs", "src/export/mod.rs"])
        ctx.parsed_files = {p: None for p in ctx.path_set}
        result = resolve_rust_import("self::export_", "src/lib.rs", ctx)
        assert result == "src/export/mod.rs"

    def test_brace_import_with_trailing_underscore(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "lib.rs").write_text("// root\n")
        (tmp_path / "src" / "export.rs").write_text("pub struct BundleOptions;\n")

        ctx = _ctx(tmp_path, ["src/lib.rs", "src/export.rs"])
        ctx.parsed_files = {p: None for p in ctx.path_set}
        result = resolve_rust_import(
            "self::export_::{BundleOptions, export}", "src/lib.rs", ctx
        )
        assert result == "src/export.rs"


class TestWorkspaceDepInheritance:
    """Cargo 1.64+ ``{ workspace = true }`` dependency inheritance."""

    def test_member_inherits_workspace_dep(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["crates/foo"]\n\n'
            '[workspace.dependencies]\n'
            'bar = { path = "../bar", package = "bar-impl" }\n'
        )
        crate_dir = tmp_path / "crates" / "foo"
        crate_dir.mkdir(parents=True)
        (crate_dir / "Cargo.toml").write_text(
            '[package]\nname = "foo"\nversion = "0.1.0"\n\n'
            '[dependencies]\nbar = { workspace = true }\n'
        )
        (crate_dir / "src").mkdir()
        (crate_dir / "src" / "lib.rs").write_text("// root\n")
        ctx = _ctx(tmp_path, ["crates/foo/src/lib.rs"])
        idx = get_or_build_cargo_workspace_index(ctx)
        assert idx is not None
        foo = next(c for c in idx.crates if c.name == "foo")
        assert any(
            d.name == "bar" and d.is_path and d.package == "bar-impl"
            for d in foo.dependencies
        )

    def test_member_inherits_simple_version(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["crates/foo"]\n\n'
            '[workspace.dependencies]\nserde = "1.0"\n'
        )
        crate_dir = tmp_path / "crates" / "foo"
        crate_dir.mkdir(parents=True)
        (crate_dir / "Cargo.toml").write_text(
            '[package]\nname = "foo"\nversion = "0.1.0"\n\n'
            '[dependencies]\nserde = { workspace = true }\n'
        )
        (crate_dir / "src").mkdir()
        (crate_dir / "src" / "lib.rs").write_text("// root\n")
        ctx = _ctx(tmp_path, ["crates/foo/src/lib.rs"])
        idx = get_or_build_cargo_workspace_index(ctx)
        assert idx is not None
        foo = next(c for c in idx.crates if c.name == "foo")
        assert any(d.name == "serde" and not d.is_path for d in foo.dependencies)


class TestBinTargetDiscovery:
    """``[[bin]]`` section parsing in Cargo.toml."""

    def test_bin_paths_parsed(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["crates/foo"]\n'
        )
        crate_dir = tmp_path / "crates" / "foo"
        crate_dir.mkdir(parents=True)
        (crate_dir / "Cargo.toml").write_text(
            '[package]\nname = "foo"\nversion = "0.1.0"\n\n'
            '[[bin]]\nname = "cli"\npath = "src/bin/cli.rs"\n\n'
            '[[bin]]\nname = "server"\npath = "src/bin/server.rs"\n'
        )
        (crate_dir / "src").mkdir()
        (crate_dir / "src" / "lib.rs").write_text("// root\n")
        ctx = _ctx(tmp_path, ["crates/foo/src/lib.rs"])
        idx = get_or_build_cargo_workspace_index(ctx)
        assert idx is not None
        foo = next(c for c in idx.crates if c.name == "foo")
        assert "crates/foo/src/bin/cli.rs" in foo.bin_paths
        assert "crates/foo/src/bin/server.rs" in foo.bin_paths

    def test_no_bin_section(self, tmp_path: Path) -> None:
        _write_workspace(tmp_path, ["crates/foo"])
        _write_member_crate(tmp_path, "crates/foo", "foo")
        ctx = _ctx(tmp_path, ["crates/foo/src/lib.rs"])
        idx = get_or_build_cargo_workspace_index(ctx)
        assert idx is not None
        foo = next(c for c in idx.crates if c.name == "foo")
        assert foo.bin_paths == ()


def _parsed_with_imports(import_specs: list[tuple[str, bool, list[str]]]):
    """Fake ParsedFile carrying Import-shaped objects (module_path, is_reexport, names)."""
    from types import SimpleNamespace

    return SimpleNamespace(
        imports=[
            SimpleNamespace(module_path=mp, is_reexport=rx, imported_names=names)
            for mp, rx, names in import_specs
        ]
    )


class TestPubUseReexportFollowing:
    def test_prelude_pattern_within_crate(self, tmp_path: Path) -> None:
        # lib.rs: pub use crate::engine::Engine; app.rs: use crate::Engine
        paths = ["src/lib.rs", "src/engine.rs", "src/app.rs"]
        ctx = _ctx(tmp_path, paths)
        ctx.parsed_files = {
            "src/lib.rs": _parsed_with_imports(
                [("crate::engine::Engine", True, ["Engine"])]
            ),
            "src/engine.rs": _parsed_with_imports([]),
            "src/app.rs": _parsed_with_imports([]),
        }
        got = resolve_rust_import("crate::Engine", "src/app.rs", ctx)
        assert got == "src/engine.rs"

    def test_workspace_cross_crate_reexport(self, tmp_path: Path) -> None:
        _write_workspace(tmp_path, ["crates/core", "crates/app"])
        _write_member_crate(tmp_path, "crates/core", "my_core")
        _write_member_crate(tmp_path, "crates/app", "my_app")
        paths = [
            "crates/core/src/lib.rs",
            "crates/core/src/store.rs",
            "crates/app/src/lib.rs",
            "crates/app/src/main.rs",
        ]
        ctx = _ctx(tmp_path, paths)
        ctx.parsed_files = {
            "crates/core/src/lib.rs": _parsed_with_imports(
                [("crate::store::Store", True, ["Store"])]
            ),
            "crates/core/src/store.rs": _parsed_with_imports([]),
            "crates/app/src/lib.rs": _parsed_with_imports([]),
            "crates/app/src/main.rs": _parsed_with_imports([]),
        }
        got = resolve_rust_import("my_core::Store", "crates/app/src/main.rs", ctx)
        assert got == "crates/core/src/store.rs"

    def test_brace_group_reexport(self, tmp_path: Path) -> None:
        paths = ["src/lib.rs", "src/types.rs", "src/app.rs"]
        ctx = _ctx(tmp_path, paths)
        ctx.parsed_files = {
            "src/lib.rs": _parsed_with_imports(
                [("crate::types::{Foo, Bar}", True, ["Foo", "Bar"])]
            ),
            "src/types.rs": _parsed_with_imports([]),
            "src/app.rs": _parsed_with_imports([]),
        }
        got = resolve_rust_import("crate::Bar", "src/app.rs", ctx)
        assert got == "src/types.rs"

    def test_glob_reexport(self, tmp_path: Path) -> None:
        paths = ["src/lib.rs", "src/prelude.rs", "src/app.rs"]
        ctx = _ctx(tmp_path, paths)
        ctx.parsed_files = {
            "src/lib.rs": _parsed_with_imports([("crate::prelude::*", True, ["*"])]),
            "src/prelude.rs": _parsed_with_imports([]),
            "src/app.rs": _parsed_with_imports([]),
        }
        got = resolve_rust_import("crate::Anything", "src/app.rs", ctx)
        assert got == "src/prelude.rs"

    def test_non_reexport_use_not_followed(self, tmp_path: Path) -> None:
        # A plain (non-pub) use in lib.rs is not part of the crate's API.
        paths = ["src/lib.rs", "src/engine.rs", "src/app.rs"]
        ctx = _ctx(tmp_path, paths)
        ctx.parsed_files = {
            "src/lib.rs": _parsed_with_imports(
                [("crate::engine::Engine", False, ["Engine"])]
            ),
            "src/engine.rs": _parsed_with_imports([]),
            "src/app.rs": _parsed_with_imports([]),
        }
        got = resolve_rust_import("crate::Engine", "src/app.rs", ctx)
        assert got is None

    def test_reexport_cycle_does_not_recurse(self, tmp_path: Path) -> None:
        # lib.rs re-exporting an unresolvable name through itself must
        # terminate (depth cap), not recurse.
        paths = ["src/lib.rs", "src/app.rs"]
        ctx = _ctx(tmp_path, paths)
        ctx.parsed_files = {
            "src/lib.rs": _parsed_with_imports([("crate::Ghost", True, ["Ghost"])]),
            "src/app.rs": _parsed_with_imports([]),
        }
        got = resolve_rust_import("crate::Ghost", "src/app.rs", ctx)
        assert got is None
