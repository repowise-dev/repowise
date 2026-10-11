"""Unit tests for the shell ``source`` / ``.`` import resolver.

Parser contract: ``parser.py`` strips the surrounding quotes from the captured
``@import.module`` text before the resolver runs, so every input below is
unquoted — ``source "$SCRIPT_DIR/x.sh"`` reaches the resolver as
``$SCRIPT_DIR/x.sh``.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.shell import resolve_shell_import


def _ctx(paths: set[str]) -> ResolverContext:
    return ResolverContext(path_set=set(paths), stem_map={}, graph=nx.DiGraph())


class TestLiteralRelative:
    def test_dot_slash_sibling(self) -> None:
        ctx = _ctx({"scripts/helpers.sh", "scripts/run.sh"})
        assert resolve_shell_import("./helpers.sh", "scripts/run.sh", ctx) == "scripts/helpers.sh"

    def test_subdir(self) -> None:
        ctx = _ctx({"scripts/lib/util.sh", "scripts/run.sh"})
        assert resolve_shell_import("lib/util.sh", "scripts/run.sh", ctx) == "scripts/lib/util.sh"

    def test_parent_relative(self) -> None:
        ctx = _ctx({"a/b/x.sh", "scripts/run.sh"})
        assert resolve_shell_import("../a/b/x.sh", "scripts/run.sh", ctx) == "a/b/x.sh"

    def test_extension_omitted(self) -> None:
        # `source lib/util` with no extension — probe the shell suffixes.
        ctx = _ctx({"scripts/lib/util.sh", "scripts/run.sh"})
        assert resolve_shell_import("lib/util", "scripts/run.sh", ctx) == "scripts/lib/util.sh"


class TestDirAnchorIdioms:
    def test_script_dir_variable(self) -> None:
        ctx = _ctx({"scripts/lib/util.sh", "scripts/run.sh"})
        got = resolve_shell_import("$SCRIPT_DIR/lib/util.sh", "scripts/run.sh", ctx)
        assert got == "scripts/lib/util.sh"

    def test_dirname_command_substitution(self) -> None:
        ctx = _ctx({"scripts/helpers.sh", "scripts/run.sh"})
        got = resolve_shell_import('$(dirname "$0")/helpers.sh', "scripts/run.sh", ctx)
        assert got == "scripts/helpers.sh"

    def test_bash_source_parameter_expansion(self) -> None:
        # ${BASH_SOURCE%/*} contains a `/` inside the braces — the resolver
        # must strip the whole ${...} segment, not split on the first slash.
        ctx = _ctx({"scripts/lib/log.sh", "scripts/run.sh"})
        got = resolve_shell_import("${BASH_SOURCE%/*}/lib/log.sh", "scripts/run.sh", ctx)
        assert got == "scripts/lib/log.sh"

    def test_backtick_dirname(self) -> None:
        ctx = _ctx({"scripts/lib/util.sh", "scripts/run.sh"})
        got = resolve_shell_import('`dirname "$0"`/lib/util.sh', "scripts/run.sh", ctx)
        assert got == "scripts/lib/util.sh"


class TestExternalFallback:
    def test_absolute_path_is_external(self) -> None:
        ctx = _ctx({"scripts/run.sh"})
        got = resolve_shell_import("/usr/lib/system.sh", "scripts/run.sh", ctx)
        assert got == "external:/usr/lib/system.sh"

    def test_remaining_interpolation_is_external(self) -> None:
        ctx = _ctx({"scripts/run.sh"})
        got = resolve_shell_import("$ROOT/$SUB/x.sh", "scripts/run.sh", ctx)
        assert got == "external:$ROOT/$SUB/x.sh"

    def test_unresolved_relative_is_external(self) -> None:
        # A wrong source edge is worse than none — unmatched literals go
        # external rather than a stem guess.
        ctx = _ctx({"scripts/run.sh"})
        got = resolve_shell_import("./missing.sh", "scripts/run.sh", ctx)
        assert got == "external:./missing.sh"

    def test_home_relative_is_external(self) -> None:
        ctx = _ctx({"scripts/run.sh"})
        got = resolve_shell_import("~/dotfiles/env.sh", "scripts/run.sh", ctx)
        assert got == "external:~/dotfiles/env.sh"

    def test_empty_returns_none(self) -> None:
        ctx = _ctx({"scripts/run.sh"})
        assert resolve_shell_import("", "scripts/run.sh", ctx) is None
