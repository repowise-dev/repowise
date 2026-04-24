"""Unit tests for the Luau import resolver.

Covers the two resolution modes implemented in this PR:

- String-literal requires (``require("relative/path")``)
- ``script`` / ``script.Parent`` relative instance paths

The ``game.<Service>.Path`` absolute form requires Rojo project JSON and is
explicitly deferred to a follow-up (issue #52); see the xfail case.

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
import pytest

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

    def test_luaurc_alias_is_external_until_follow_up(self) -> None:
        # `.luaurc` `@alias` resolution is deferred — see
        # TestLuaurcAlias.test_alias_resolves_via_luaurc_follow_up.
        ctx = _ctx({"src/dependency.luau"})
        got = resolve_luau_import("@dep", "src/main.luau", ctx)
        assert got == "external:@dep"


class TestAbsoluteInstancePath:
    @pytest.mark.xfail(
        reason="Rojo default.project.json-aware resolution — issue #52 follow-up.",
        strict=True,
    )
    def test_game_replicated_storage_resolves_via_rojo_tree(self) -> None:
        """Expected end state: given a Rojo project whose tree maps
        ``ReplicatedStorage.Shared`` to ``src/shared``, a require of
        ``game.ReplicatedStorage.Shared.Util`` resolves to
        ``src/shared/Util.luau``.
        """
        ctx = _ctx({"src/shared/Util.luau"})
        got = resolve_luau_import("game.ReplicatedStorage.Shared.Util", "src/client/main.luau", ctx)
        assert got == "src/shared/Util.luau"
