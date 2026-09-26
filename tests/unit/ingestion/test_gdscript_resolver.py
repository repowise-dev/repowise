"""Unit tests for the GDScript import resolver.

The interesting behaviour is that ``res://`` is absolute from the *Godot
project* root -- the directory holding ``project.godot`` -- not the repo
root. A single repo can hold many projects (``godot-demo-projects``, the
tier-1 validation corpus, is exactly that shape), so these tests pin the
nearest-enclosing-project rule and the repo-root fallback.

``uid://`` is the other half: Godot 4.4 addresses a resource by uid, and the
mapping lives in the one-line ``<file>.uid`` sidecar beside it. Those tests
write the sidecars to disk, since the resolver reads them the same way it
reads ``project.godot``.

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


def _write_uid(root: Path, relative: str, uid: str) -> None:
    """Write *relative*, plus the one-line ``.uid`` sidecar Godot puts beside it."""
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("extends Node\n", encoding="utf-8")
    sidecar = root / f"{relative}.uid"
    sidecar.write_text(uid + "\n", encoding="utf-8")


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

    def test_missing_uid_becomes_external(self, tmp_path: Path) -> None:
        # A uid no sidecar carries: nothing in the repo owns it, so it is a
        # miss like any other and stays visible as an external node.
        ctx = _ctx({"a.gd"}, repo_path=tmp_path)
        got = resolve_gdscript_import("uid://cxy8n1e0abcde", "a.gd", ctx)
        assert got == "external:uid://cxy8n1e0abcde"

    def test_uid_outside_any_sidecar_is_external_with_no_repo_path(
        self, tmp_path: Path
    ) -> None:
        # No repo_path at all (a bare context): the scan has nothing to read,
        # and the reference still has to come back as a miss, not raise.
        ctx = _ctx({"a.gd"})
        got = resolve_gdscript_import("uid://cxy8n1e0abcde", "a.gd", ctx)
        assert got == "external:uid://cxy8n1e0abcde"

    def test_user_paths_are_external(self, tmp_path: Path) -> None:
        ctx = _ctx({"a.gd"}, repo_path=tmp_path)
        assert resolve_gdscript_import("user://save.dat", "a.gd", ctx) == "external:user://save.dat"

    def test_empty_module_path_resolves_to_nothing(self, tmp_path: Path) -> None:
        ctx = _ctx({"a.gd"}, repo_path=tmp_path)
        assert resolve_gdscript_import("   ", "a.gd", ctx) is None


class TestUidPaths:
    """Godot 4.4 addresses resources by uid; the sidecar is the mapping."""

    def test_uid_resolves_to_the_file_its_sidecar_names(self, tmp_path: Path) -> None:
        # The exact shape of the issue: `2d/bullet_shower/bullets.gd.uid`
        # holds `uid://e3fsq0i746o1`, and a scene referencing that uid must
        # reach bullets.gd.
        _write_uid(tmp_path, "bullets.gd", "uid://e3fsq0i746o1")
        ctx = _ctx({"bullets.gd", "shower.tscn"}, repo_path=tmp_path)
        got = resolve_gdscript_import("uid://e3fsq0i746o1", "shower.tscn", ctx)
        assert got == "bullets.gd"

    def test_uid_resolves_across_directories(self, tmp_path: Path) -> None:
        # The mapped path is the sidecar's own path minus `.uid`, so the
        # directory the sidecar sits in comes along with it.
        _write_uid(tmp_path, "actors/player.gd", "uid://bwhlkliwp13p4")
        ctx = _ctx({"actors/player.gd", "main.gd"}, repo_path=tmp_path)
        got = resolve_gdscript_import("uid://bwhlkliwp13p4", "main.gd", ctx)
        assert got == "actors/player.gd"

    def test_uid_resolves_for_a_scene_target(self, tmp_path: Path) -> None:
        # `[ext_resource type="PackedScene" uid="..." path="res://enemy.tscn"]`
        # and a path-less preload of one. A scene has a sidecar too.
        (tmp_path / "enemy.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")
        (tmp_path / "enemy.tscn.uid").write_text("uid://cao351pllxqpa\n", encoding="utf-8")
        ctx = _ctx({"main.gd", "enemy.tscn"}, repo_path=tmp_path)
        got = resolve_gdscript_import("uid://cao351pllxqpa", "main.gd", ctx)
        assert got == "enemy.tscn"

    def test_uid_with_no_sidecar_still_misses(self, tmp_path: Path) -> None:
        _write_uid(tmp_path, "bullets.gd", "uid://e3fsq0i746o1")
        ctx = _ctx({"bullets.gd", "shower.tscn"}, repo_path=tmp_path)
        got = resolve_gdscript_import("uid://nosuchuid00000", "shower.tscn", ctx)
        assert got == "external:uid://nosuchuid00000"

    def test_uid_pointing_at_an_unindexed_file_misses(self, tmp_path: Path) -> None:
        # The sidecar names a `.gdshader`, which repowise has no language for,
        # so the file is not in path_set and the reference must not become an
        # edge to a path the graph holds no node for.
        (tmp_path / "fx").mkdir()
        (tmp_path / "fx" / "wave.gdshader").write_text("shader_type canvas_item;\n", encoding="utf-8")
        (tmp_path / "fx" / "wave.gdshader.uid").write_text(
            "uid://da1shad0r0001\n", encoding="utf-8"
        )
        ctx = _ctx({"wave.gd"}, repo_path=tmp_path)
        got = resolve_gdscript_import("uid://da1shad0r0001", "wave.gd", ctx)
        assert got == "external:uid://da1shad0r0001"

    def test_a_uid_two_sidecars_claim_is_refused(self, tmp_path: Path) -> None:
        # A vendored or copy-pasted project can ship one resource twice, so
        # two sidecars carry one uid. Both are equally its owner, and a wrong
        # edge is worse than none: refuse rather than pick.
        _write_uid(tmp_path, "2d/dodge/main.gd", "uid://c4wt6ace7hycd")
        _write_uid(tmp_path, "2d/draw/main.gd", "uid://c4wt6ace7hycd")
        ctx = _ctx({"2d/dodge/main.gd", "2d/draw/main.gd"}, repo_path=tmp_path)
        got = resolve_gdscript_import("uid://c4wt6ace7hycd", "2d/dodge/main.gd", ctx)
        assert got == "external:uid://c4wt6ace7hycd"

    def test_the_uid_map_is_scanned_once_per_context(self, tmp_path: Path) -> None:
        _write_uid(tmp_path, "bullets.gd", "uid://e3fsq0i746o1")
        ctx = _ctx({"bullets.gd", "shower.tscn"}, repo_path=tmp_path)
        assert (
            resolve_gdscript_import("uid://e3fsq0i746o1", "shower.tscn", ctx) == "bullets.gd"
        )

        # Deleting the sidecar after the first call must not change the
        # answer: the map was cached on the context, not rebuilt per lookup.
        (tmp_path / "bullets.gd.uid").unlink()
        got = resolve_gdscript_import("uid://e3fsq0i746o1", "shower.tscn", ctx)
        assert got == "bullets.gd"

    def test_a_sidecar_bom_does_not_hide_the_uid(self, tmp_path: Path) -> None:
        # `utf-8-sig`: a BOM on the sidecar's single line would otherwise
        # leave the uid prefixed and never match.
        _write_uid(tmp_path, "bullets.gd", "\ufeffuid://e3fsq0i746o1")
        ctx = _ctx({"bullets.gd", "shower.tscn"}, repo_path=tmp_path)
        got = resolve_gdscript_import("uid://e3fsq0i746o1", "shower.tscn", ctx)
        assert got == "bullets.gd"

    def test_a_sidecar_with_no_uid_prefix_is_ignored(self, tmp_path: Path) -> None:
        # Only `uid://...` content maps. Anything else in a `.uid` file (a
        # truncated write, a hand-made file) is not a reference target.
        target = tmp_path / "bullets.gd"
        target.write_text("extends Node\n", encoding="utf-8")
        (tmp_path / "bullets.gd.uid").write_text("not-a-uid\n", encoding="utf-8")
        ctx = _ctx({"bullets.gd"}, repo_path=tmp_path)
        got = resolve_gdscript_import("uid://not-a-uid", "bullets.gd", ctx)
        assert got == "external:uid://not-a-uid"

    def test_a_file_named_only_dot_uid_names_nothing(self, tmp_path: Path) -> None:
        # `iter_glob("*.uid")` also yields a file literally named `.uid`, whose
        # stripped "path" is empty. The guard is on the basename: a nested one
        # (`sub/.uid`) strips to the directory `sub`, which is not a file and
        # would never match `path_set`, but the empty case must not reach the
        # map either.
        (tmp_path / ".uid").write_text("uid://e3fsq0i746o1\n", encoding="utf-8")
        ctx = _ctx({"bullets.gd"}, repo_path=tmp_path)
        got = resolve_gdscript_import("uid://e3fsq0i746o1", "bullets.gd", ctx)
        assert got == "external:uid://e3fsq0i746o1"

    def test_user_scheme_is_unaffected_by_the_uid_map(self, tmp_path: Path) -> None:
        # `user://` shares the branch the uid handling came out of. It is a
        # runtime path with no repository counterpart, and must keep
        # refusing even when sidecars exist.
        _write_uid(tmp_path, "bullets.gd", "uid://e3fsq0i746o1")
        ctx = _ctx({"bullets.gd", "shower.tscn"}, repo_path=tmp_path)
        got = resolve_gdscript_import("user://save.dat", "shower.tscn", ctx)
        assert got == "external:user://save.dat"


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
