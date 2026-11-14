"""Unit tests for the Perl ``use`` / ``require`` import resolver.

Parser contract: quotes are stripped from the captured ``@import.module`` text
before the resolver runs, so ``require "lib/x.pl"`` arrives as ``lib/x.pl``.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.perl import resolve_perl_import


def _ctx(paths: set[str]) -> ResolverContext:
    return ResolverContext(path_set=set(paths), stem_map={}, graph=nx.DiGraph())


class TestModules:
    def test_module_under_lib(self) -> None:
        ctx = _ctx({"lib/My/Thing.pm", "bin/run.pl"})
        assert resolve_perl_import("My::Thing", "bin/run.pl", ctx) == "lib/My/Thing.pm"

    def test_single_segment_module(self) -> None:
        ctx = _ctx({"lib/Util.pm", "bin/run.pl"})
        assert resolve_perl_import("Util", "bin/run.pl", ctx) == "lib/Util.pm"

    def test_nearest_copy_wins_a_tie(self) -> None:
        ctx = _ctx({"a/lib/X/Y.pm", "b/lib/X/Y.pm", "a/bin/run.pl"})
        assert resolve_perl_import("X::Y", "a/bin/run.pl", ctx) == "a/lib/X/Y.pm"

    def test_equal_distance_tie_links_nothing(self) -> None:
        ctx = _ctx({"a/lib/X/Y.pm", "b/lib/X/Y.pm", "run.pl"})
        assert resolve_perl_import("X::Y", "run.pl", ctx) == "external:X::Y"

    def test_suffix_must_be_whole_segments(self) -> None:
        ctx = _ctx({"lib/MyThing.pm", "run.pl"})
        assert resolve_perl_import("Thing", "run.pl", ctx) == "external:Thing"

    def test_cpan_module_is_external(self) -> None:
        ctx = _ctx({"run.pl"})
        assert resolve_perl_import("File::Spec", "run.pl", ctx) == "external:File::Spec"


class TestPragmas:
    def test_pragmas_produce_no_node(self) -> None:
        ctx = _ctx({"run.pl"})
        for pragma in ("strict", "warnings", "parent", "constant", "utf8"):
            assert resolve_perl_import(pragma, "run.pl", ctx) is None


class TestFiles:
    def test_importer_relative(self) -> None:
        ctx = _ctx({"bin/lib/helper.pl", "bin/run.pl"})
        assert resolve_perl_import("lib/helper.pl", "bin/run.pl", ctx) == "bin/lib/helper.pl"

    def test_repo_root_relative(self) -> None:
        ctx = _ctx({"lib/helper.pl", "bin/run.pl"})
        assert resolve_perl_import("lib/helper.pl", "bin/run.pl", ctx) == "lib/helper.pl"

    def test_interpolated_path_is_external(self) -> None:
        ctx = _ctx({"run.pl"})
        assert resolve_perl_import("$ENV{HOME}/x.pl", "run.pl", ctx) == "external:$ENV{HOME}/x.pl"
