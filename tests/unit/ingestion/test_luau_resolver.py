"""Unit tests for the Luau import resolver.

Covers the two resolution modes implemented in this PR:

- String-literal requires (``require("relative/path")``)
- ``script`` / ``script.Parent`` relative instance paths
- ``game.<Service>.Path`` absolute instance paths via Rojo
  ``default.project.json`` (issue #52)
- ``@alias`` requires via ``.luaurc`` aliases (issue #52)

Parser contract
---------------
The arguments passed to ``resolve_luau_import`` mirror what the production
parser emits: ``parser.py`` strips surrounding quotes from the captured
``@import.module`` text (``.strip("\"'` ")``) before calling the resolver.
String-literal tests therefore pass *unquoted* paths (``"./helper"`` becomes
``./helper``) — passing a quoted string here would not reflect the real
production handoff.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.luau import resolve_luau_import


def _ctx(paths: set[str]) -> ResolverContext:
    stem_map: dict[str, list[str]] = {}
    for p in paths:
        stem = p.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        stem_map.setdefault(stem, []).append(p)
    return ResolverContext(
        path_set=paths,
        stem_map=stem_map,
        graph=nx.DiGraph(),
    )


class TestScriptRelative:
    def test_sibling_via_parent(self) -> None:
        ctx = _ctx({"src/shared/Signal.luau", "src/client/main.luau"})
        got = resolve_luau_import("script.Parent.Signal", "src/client/main.luau", ctx)
        # script.Parent == src/client, so the sibling is src/client/Signal.luau
        # -- this case has no match. script.Parent.Parent.shared.Signal would.
        # The resolver should return external rather than wrong-file match.
        assert got == "external:script.Parent.Signal"

    def test_child_module(self) -> None:
        ctx = _ctx({"src/shared/util/init.luau", "src/shared/util/Signal.luau"})
        got = resolve_luau_import("script.Signal", "src/shared/util/init.luau", ctx)
        assert got == "src/shared/util/Signal.luau"

    def test_parent_walks_up(self) -> None:
        ctx = _ctx({"src/shared/Signal.luau", "src/client/controllers/main.luau"})
        got = resolve_luau_import(
            "script.Parent.Parent.Parent.shared.Signal",
            "src/client/controllers/main.luau",
            ctx,
        )
        assert got == "src/shared/Signal.luau"

    def test_module_as_directory(self) -> None:
        ctx = _ctx({"src/shared/util/init.lua", "src/shared/main.luau"})
        got = resolve_luau_import("script.Parent.util", "src/shared/main.luau", ctx)
        assert got == "src/shared/util/init.lua"


class TestScriptRelativeWithInstanceMethods:
    # Rojo-safe idioms — on OSRPS these account for ~93% of `require(...)`.
    # They must resolve identically to the dot-chain forms in TestScriptRelative.

    def test_wait_for_child_child_module(self) -> None:
        ctx = _ctx({"src/shared/util/init.luau", "src/shared/util/Signal.luau"})
        got = resolve_luau_import('script:WaitForChild("Signal")', "src/shared/util/init.luau", ctx)
        assert got == "src/shared/util/Signal.luau"

    def test_wait_for_child_mixed_with_parent(self) -> None:
        ctx = _ctx({"src/shared/Signal.luau", "src/client/main.luau"})
        got = resolve_luau_import(
            'script.Parent.Parent:WaitForChild("shared"):WaitForChild("Signal")',
            "src/client/main.luau",
            ctx,
        )
        assert got == "src/shared/Signal.luau"

    def test_find_first_child_sibling(self) -> None:
        ctx = _ctx({"src/shared/util/init.luau", "src/shared/util/Signal.luau"})
        got = resolve_luau_import(
            'script:FindFirstChild("Signal")', "src/shared/util/init.luau", ctx
        )
        assert got == "src/shared/util/Signal.luau"

    def test_wait_for_child_with_timeout_arg(self) -> None:
        # Roblox `WaitForChild(name, timeoutSeconds)` — timeout is discarded.
        ctx = _ctx({"src/shared/util/init.luau", "src/shared/util/Signal.luau"})
        got = resolve_luau_import(
            'script:WaitForChild("Signal", 5)', "src/shared/util/init.luau", ctx
        )
        assert got == "src/shared/util/Signal.luau"

    def test_unresolved_wait_for_child_preserves_original_text(self) -> None:
        # The graph's external-node label should match what the user wrote,
        # not the post-normalization form — readers shouldn't see a rewritten
        # `.Foo` when their code says `:WaitForChild("Foo")`.
        ctx = _ctx(set())
        got = resolve_luau_import('script.Parent:WaitForChild("Missing")', "src/a.luau", ctx)
        assert got == 'external:script.Parent:WaitForChild("Missing")'


class TestStringLiteral:
    # The parser strips quotes at parser.py:705 before the resolver runs,
    # so every input below is unquoted — matching production.  An earlier
    # version of this test class passed *quoted* strings, which masked a
    # real bug: the resolver's string-literal branch was unreachable in
    # production and every `require("…")` landed on the external fallback.

    def test_relative_string(self) -> None:
        ctx = _ctx({"src/shared/helper.luau", "src/shared/main.luau"})
        got = resolve_luau_import("./helper", "src/shared/main.luau", ctx)
        assert got == "src/shared/helper.luau"

    def test_parent_relative_string(self) -> None:
        ctx = _ctx({"bench/bench_support.lua", "bench/gc/test_foo.lua"})
        got = resolve_luau_import("../bench_support", "bench/gc/test_foo.lua", ctx)
        assert got == "bench/bench_support.lua"

    def test_stem_match_bare_path(self) -> None:
        ctx = _ctx({"src/shared/helper.luau", "src/main.luau"})
        got = resolve_luau_import("helper", "src/main.luau", ctx)
        assert got == "src/shared/helper.luau"

    def test_unresolved_string_goes_external(self) -> None:
        ctx = _ctx(set())
        got = resolve_luau_import("nowhere", "src/a.luau", ctx)
        assert got == "external:nowhere"

    def test_alias_without_luaurc_is_external(self) -> None:
        # No .luaurc anywhere (no repo_path at all here) → the alias keeps
        # the external-node fallback instead of guessing a file.
        ctx = _ctx({"src/dependency.luau"})
        got = resolve_luau_import("@dep", "src/main.luau", ctx)
        assert got == "external:@dep"


def _repo_ctx(tmp_path, paths: set[str], files: dict[str, str]) -> ResolverContext:
    """Context with a real repo dir holding config *files* (Rojo/.luaurc)."""
    for rel, text in files.items():
        full = tmp_path / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text)
    ctx = _ctx(paths)
    ctx.repo_path = tmp_path
    return ctx


class TestAbsoluteInstancePath:
    def test_game_replicated_storage_resolves_via_rojo_tree(self, tmp_path) -> None:
        """Given a Rojo project whose tree maps ``ReplicatedStorage.Shared``
        to ``src/shared``, ``game.ReplicatedStorage.Shared.Util`` resolves to
        ``src/shared/Util.luau`` (issue #52).
        """
        project = """{
  "name": "MyGame",
  "tree": {
    "$className": "DataModel",
    "ReplicatedStorage": {
      "Shared": { "$path": "src/shared" }
    }
  }
}"""
        ctx = _repo_ctx(
            tmp_path, {"src/shared/Util.luau"}, {"default.project.json": project}
        )
        got = resolve_luau_import(
            "game.ReplicatedStorage.Shared.Util", "src/client/main.luau", ctx
        )
        assert got == "src/shared/Util.luau"

    def test_get_service_idiom_resolves(self, tmp_path) -> None:
        project = """{
  "tree": {
    "ReplicatedStorage": { "Shared": { "$path": "src/shared" } }
  }
}"""
        ctx = _repo_ctx(
            tmp_path, {"src/shared/Util.luau"}, {"default.project.json": project}
        )
        got = resolve_luau_import(
            'game:GetService("ReplicatedStorage").Shared.Util',
            "src/client/main.luau",
            ctx,
        )
        assert got == "src/shared/Util.luau"

    def test_nested_tree_and_module_directory(self, tmp_path) -> None:
        # Deeper instance nesting + module-as-directory (init.luau).
        project = """{
  "tree": {
    "ServerScriptService": {
      "Server": {
        "Systems": { "$path": "src/server/systems" }
      }
    }
  }
}"""
        ctx = _repo_ctx(
            tmp_path,
            {"src/server/systems/Combat/init.luau"},
            {"default.project.json": project},
        )
        got = resolve_luau_import(
            "game.ServerScriptService.Server.Systems.Combat", "src/main.luau", ctx
        )
        assert got == "src/server/systems/Combat/init.luau"

    def test_missing_project_file_falls_back_to_external(self, tmp_path) -> None:
        # Current behavior preserved: no default.project.json → external node.
        ctx = _repo_ctx(tmp_path, {"src/shared/Util.luau"}, {})
        got = resolve_luau_import(
            "game.ReplicatedStorage.Shared.Util", "src/client/main.luau", ctx
        )
        assert got == "external:game.ReplicatedStorage.Shared.Util"

    def test_unmapped_instance_path_goes_external(self, tmp_path) -> None:
        project = '{ "tree": { "ReplicatedStorage": { "$path": "src/shared" } } }'
        ctx = _repo_ctx(
            tmp_path, {"src/shared/Util.luau"}, {"default.project.json": project}
        )
        got = resolve_luau_import("game.Workspace.Thing", "src/main.luau", ctx)
        assert got == "external:game.Workspace.Thing"


class TestLuaurcAlias:
    def test_alias_resolves_via_luaurc(self, tmp_path) -> None:
        """A ``.luaurc`` above the importer declaring
        ``{"aliases": {"dep": "./dependency"}}`` makes ``@dep`` resolve to
        ``src/dependency.luau`` (issue #52's second half — on
        luau-lang/luau, 24 ``@alias`` requires previously all landed on the
        external-node fallback).
        """
        ctx = _repo_ctx(
            tmp_path,
            {"src/dependency.luau"},
            {"src/.luaurc": '{"aliases": {"dep": "./dependency"}}'},
        )
        got = resolve_luau_import("@dep", "src/main.luau", ctx)
        assert got == "src/dependency.luau"

    def test_alias_subpath_and_comments(self, tmp_path) -> None:
        luaurc = """{
  // shared code lives here
  "aliases": { "shared": "./shared" }
}"""
        ctx = _repo_ctx(
            tmp_path,
            {"src/shared/util/Signal.luau"},
            {"src/.luaurc": luaurc},
        )
        got = resolve_luau_import("@shared/util/Signal", "src/client/main.luau", ctx)
        assert got == "src/shared/util/Signal.luau"

    def test_child_luaurc_overrides_parent(self, tmp_path) -> None:
        # Parent maps @dep to ./a, the importer's own dir remaps it to ./b —
        # the nearest declaration wins.
        ctx = _repo_ctx(
            tmp_path,
            {"a.luau", "src/b.luau"},
            {
                ".luaurc": '{"aliases": {"dep": "./a"}}',
                "src/.luaurc": '{"aliases": {"dep": "./b"}}',
            },
        )
        got = resolve_luau_import("@dep", "src/main.luau", ctx)
        assert got == "src/b.luau"

    def test_parent_alias_visible_from_subdir(self, tmp_path) -> None:
        ctx = _repo_ctx(
            tmp_path,
            {"libs/dep/init.luau"},
            {".luaurc": '{"aliases": {"dep": "./libs/dep"}}'},
        )
        got = resolve_luau_import("@dep", "src/deeply/nested/main.luau", ctx)
        assert got == "libs/dep/init.luau"

    def test_unknown_alias_goes_external(self, tmp_path) -> None:
        ctx = _repo_ctx(
            tmp_path,
            {"src/dependency.luau"},
            {"src/.luaurc": '{"aliases": {"dep": "./dependency"}}'},
        )
        got = resolve_luau_import("@nope", "src/main.luau", ctx)
        assert got == "external:@nope"


class TestBareScriptParent:
    def test_spec_requires_sibling_init_module(self, tmp_path) -> None:
        # Roblox idiom: lib/init.spec.lua tests its own module with
        # ``require(script.Parent)`` — the container directory IS the
        # module (backed by its init.lua).
        ctx = _ctx({"lib/init.lua", "lib/init.spec.lua"})
        resolved = resolve_luau_import("script.Parent", "lib/init.spec.lua", ctx)
        assert resolved == "lib/init.lua"

    def test_bare_parent_without_init_goes_external(self, tmp_path) -> None:
        ctx = _ctx({"lib/foo.lua", "lib/foo.spec.lua"})
        resolved = resolve_luau_import("script.Parent", "lib/foo.spec.lua", ctx)
        assert resolved is None or resolved.startswith("external:")

    def test_bare_parent_never_resolves_to_importer(self, tmp_path) -> None:
        # init.lua itself saying require(script.Parent) must not self-link.
        ctx = _ctx({"lib/init.lua"})
        resolved = resolve_luau_import("script.Parent", "lib/init.lua", ctx)
        assert resolved != "lib/init.lua"
