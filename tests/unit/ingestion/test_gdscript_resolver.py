"""Unit tests for the GDScript import resolver.

The interesting behaviour is that ``res://`` is absolute from the *Godot
project* root -- the directory holding ``project.godot`` -- not the repo
root. A single repo can hold many projects (``godot-demo-projects``, the
tier-1 validation corpus, is exactly that shape), so these tests pin the
nearest-enclosing-project rule and the repo-root fallback.

Parser contract
---------------
``parser.py`` strips surrounding quotes from the captured ``@import.module``
text before calling a resolver, so these tests pass unquoted paths --
``preload("res://a.gd")`` reaches the resolver as ``res://a.gd``.

No grammar dependency here on purpose: this module must run even in a venv
without ``tree_sitter_gdscript``, so it carries no ``importorskip``.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.gdscript import resolve_gdscript_import


def _ctx(paths: set[str], repo_path: Path | None = None) -> ResolverContext:
    stem_map: dict[str, list[str]] = {}
    for p in paths:
        stem = p.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        stem_map.setdefault(stem, []).append(p)
    return ResolverContext(
        path_set=paths,
        stem_map=stem_map,
        graph=nx.DiGraph(),
        repo_path=repo_path,
    )


def _write_project(root: Path, *relative_dirs: str) -> None:
    """Drop a minimal ``project.godot`` into each of *relative_dirs*."""
    for rel in relative_dirs:
        target = root / rel if rel else root
        target.mkdir(parents=True, exist_ok=True)
        (target / "project.godot").write_text("config_version=5\n", encoding="utf-8")


class TestResPaths:
    def test_resolves_against_repo_root_when_project_is_at_root(self, tmp_path: Path) -> None:
        _write_project(tmp_path, "")
        ctx = _ctx({"actors/player.gd", "actors/base.gd"}, repo_path=tmp_path)
        got = resolve_gdscript_import("res://actors/base.gd", "actors/player.gd", ctx)
        assert got == "actors/base.gd"

    def test_resolves_against_repo_root_when_no_project_godot_exists(
        self, tmp_path: Path
    ) -> None:
        # A loose bag of scripts with no declared project boundary: the repo
        # root is the only sensible reading of res://.
        ctx = _ctx({"actors/player.gd", "actors/base.gd"}, repo_path=tmp_path)
        got = resolve_gdscript_import("res://actors/base.gd", "actors/player.gd", ctx)
        assert got == "actors/base.gd"

    def test_leading_slashes_after_the_scheme_are_tolerated(self, tmp_path: Path) -> None:
        ctx = _ctx({"a.gd", "b.gd"}, repo_path=tmp_path)
        assert resolve_gdscript_import("res:///b.gd", "a.gd", ctx) == "b.gd"


class TestMultiProjectRepo:
    """The godot-demo-projects shape: many projects, one checkout."""

    def test_nearest_project_root_wins(self, tmp_path: Path) -> None:
        _write_project(tmp_path, "2d/platformer", "3d/voxel")
        paths = {
            "2d/platformer/player.gd",
            "2d/platformer/enemy.gd",
            "3d/voxel/player.gd",
            "3d/voxel/world.gd",
        }
        ctx = _ctx(paths, repo_path=tmp_path)

        # Both projects declare a res://player.gd. Each importer must reach
        # its OWN sibling, never the other project's same-named file.
        assert (
            resolve_gdscript_import("res://player.gd", "2d/platformer/enemy.gd", ctx)
            == "2d/platformer/player.gd"
        )
        assert (
            resolve_gdscript_import("res://player.gd", "3d/voxel/world.gd", ctx)
            == "3d/voxel/player.gd"
        )

    def test_deeper_project_shadows_an_outer_one(self, tmp_path: Path) -> None:
        # A demo project nested inside an outer project: the inner
        # project.godot is the res:// root for files beneath it.
        _write_project(tmp_path, "", "demos/inner")
        paths = {"shared.gd", "demos/inner/shared.gd", "demos/inner/main.gd"}
        ctx = _ctx(paths, repo_path=tmp_path)
        got = resolve_gdscript_import("res://shared.gd", "demos/inner/main.gd", ctx)
        assert got == "demos/inner/shared.gd"

    def test_file_outside_every_project_falls_back_to_repo_root(self, tmp_path: Path) -> None:
        _write_project(tmp_path, "demos/inner")
        paths = {"tools/build.gd", "tools/helper.gd"}
        ctx = _ctx(paths, repo_path=tmp_path)
        got = resolve_gdscript_import("res://tools/helper.gd", "tools/build.gd", ctx)
        assert got == "tools/helper.gd"


class TestRelativePaths:
    def test_sibling_relative_path(self, tmp_path: Path) -> None:
        ctx = _ctx({"actors/player.gd", "actors/base.gd"}, repo_path=tmp_path)
        assert resolve_gdscript_import("base.gd", "actors/player.gd", ctx) == "actors/base.gd"

    def test_dotdot_walks_up(self, tmp_path: Path) -> None:
        ctx = _ctx({"actors/player.gd", "lib/util.gd"}, repo_path=tmp_path)
        got = resolve_gdscript_import("../lib/util.gd", "actors/player.gd", ctx)
        assert got == "lib/util.gd"

    def test_escaping_above_the_repo_root_is_external_not_clamped(
        self, tmp_path: Path
    ) -> None:
        # Clamping the `..` would fold this onto `vendor/shared/player.gd`
        # and resolve to that unrelated file. The real target is outside the
        # checkout, so the only correct answer is external.
        ctx = _ctx({"player.gd", "vendor/shared/player.gd"}, repo_path=tmp_path)
        got = resolve_gdscript_import("../../vendor/shared/player.gd", "player.gd", ctx)
        assert got == "external:../../vendor/shared/player.gd"


class TestUnresolved:
    def test_missing_target_becomes_external_not_a_stem_guess(self, tmp_path: Path) -> None:
        # `res://` is exact by construction, so a miss must NOT be rescued by
        # matching the filename somewhere else in the repo.
        ctx = _ctx({"a.gd", "somewhere/deep/base.gd"}, repo_path=tmp_path)
        got = resolve_gdscript_import("res://actors/base.gd", "a.gd", ctx)
        assert got == "external:res://actors/base.gd"

    def test_uid_paths_are_external(self, tmp_path: Path) -> None:
        ctx = _ctx({"a.gd"}, repo_path=tmp_path)
        got = resolve_gdscript_import("uid://cxy8n1e0abcde", "a.gd", ctx)
        assert got == "external:uid://cxy8n1e0abcde"

    def test_user_paths_are_external(self, tmp_path: Path) -> None:
        ctx = _ctx({"a.gd"}, repo_path=tmp_path)
        assert resolve_gdscript_import("user://save.dat", "a.gd", ctx) == "external:user://save.dat"

    def test_empty_module_path_resolves_to_nothing(self, tmp_path: Path) -> None:
        ctx = _ctx({"a.gd"}, repo_path=tmp_path)
        assert resolve_gdscript_import("   ", "a.gd", ctx) is None


class TestProjectRootScanIsCached:
    def test_roots_are_scanned_once_per_context(self, tmp_path: Path) -> None:
        _write_project(tmp_path, "game")
        ctx = _ctx({"game/a.gd", "game/b.gd"}, repo_path=tmp_path)
        resolve_gdscript_import("res://b.gd", "game/a.gd", ctx)

        # Deleting the manifest after the first call must not change the
        # answer -- proof the scan result was cached on the context rather
        # than repeated per import (this runs once per file per build).
        (tmp_path / "game" / "project.godot").unlink()
        assert resolve_gdscript_import("res://b.gd", "game/a.gd", ctx) == "game/b.gd"
