"""Per-language warmup hooks that run before the graph-import phase.

Some languages (notably C# / .NET) need an expensive one-time index
built before any per-file import can be resolved. When that build runs
lazily on first import resolution, the progress bar appears frozen for
many minutes mid-phase and the cost is silently absorbed into
``graph.imports`` timing — making it indistinguishable from real
import-resolution work.

This module gives each language a place to declare a *warmup* function
that runs in its own phase event (``graph.<lang>_index``), before the
``graph.imports`` loop starts. Warmups are gated on whether any
parsed file actually uses the language, so a Python-only repo never
pays a Java index cost.

Adding a new language's warmup is one entry in :data:`_WARMUPS`.
Implementations live in the language's resolver subpackage so this
module stays language-agnostic.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import ParsedFile
    from .resolvers import ResolverContext


# A warmup receives the resolver context and returns nothing. It may
# cache its result on ``ctx`` (the resolvers already use a per-context
# attribute cache); the dispatcher does not inspect the return value.
Warmup = Callable[["ResolverContext"], None]


def _warmup_jvm(ctx: "ResolverContext") -> None:
    from .resolvers.jvm_workspace import get_or_build_jvm_index

    get_or_build_jvm_index(ctx)


def _warmup_dotnet(ctx: "ResolverContext") -> None:
    from .resolvers.dotnet import get_or_build_index

    get_or_build_index(ctx)


def _warmup_go(ctx: "ResolverContext") -> None:
    from .resolvers.go_workspace import get_or_build_go_index

    get_or_build_go_index(ctx)


def _warmup_typescript(ctx: "ResolverContext") -> None:
    """Build the TS workspace index and stamp ``is_entry_point`` on every
    source file the workspace's ``package.json`` ``exports`` map resolves
    to. Without this, files reachable only through the package boundary
    (downstream npm consumers) read as ``in_degree==0`` and ship as
    unreachable findings.
    """
    from .resolvers.ts_workspace import (
        find_mdx_import_targets,
        find_npm_script_entry_targets,
        find_vitest_include_targets,
        get_or_build_ts_index,
    )

    index = get_or_build_ts_index(ctx)
    graph = getattr(ctx, "graph", None)
    if graph is None:
        return
    entry_paths: set[str] = set(index.exports_entry_paths)
    # MDX-only consumers (docs sites that import TSX components into
    # ``.mdx``) and custom vitest layouts (``runtime-tests/**``) — both
    # invisible to the TS parser, both real entry points.
    try:
        entry_paths |= find_mdx_import_targets(ctx)
    except Exception:
        pass
    try:
        entry_paths |= find_vitest_include_targets(ctx)
    except Exception:
        pass
    # ``package.json`` ``scripts.*`` references: benchmark / bench-runner /
    # rollup-input paths that ship as live code but are never imported
    # by the main entry graph.
    try:
        entry_paths |= find_npm_script_entry_targets(ctx)
    except Exception:
        pass
    for path in entry_paths:
        node = graph.nodes.get(path)
        if node is None:
            continue
        node["is_entry_point"] = True


# Map language tag → (phase-event name, warmup function). The phase
# name shows up in the CLI progress bar and in ``state.json`` timings.
#
# Note: ``typescript`` and ``javascript`` share a single warmup — the
# workspace index is derived from ``package.json`` files and is the
# same for both languages. The dispatcher registers under each tag so
# a JS-only repo still triggers the index build.
_WARMUPS: dict[str, tuple[str, Warmup]] = {
    "java": ("graph.jvm_index", _warmup_jvm),
    "kotlin": ("graph.jvm_index", _warmup_jvm),
    "csharp": ("graph.dotnet_index", _warmup_dotnet),
    "go": ("graph.go_index", _warmup_go),
    "typescript": ("graph.ts_index", _warmup_typescript),
    "javascript": ("graph.ts_index", _warmup_typescript),
}


def run_warmups(
    parsed_files: dict[str, "ParsedFile"],
    ctx: "ResolverContext",
    progress: Any | None = None,
) -> None:
    """Run every registered warmup whose language appears in ``parsed_files``.

    Each warmup runs under its own ``on_phase_start`` / ``on_phase_done``
    pair so phase timings attribute the cost to the language rather
    than dropping it into ``graph.imports``.
    """
    present_langs: set[str] = {pf.file_info.language for pf in parsed_files.values()}
    fired_phases: set[str] = set()
    for lang, (phase_name, warmup) in _WARMUPS.items():
        if lang not in present_langs:
            continue
        # Some warmups (TS + JS) share a phase event because they share the
        # underlying index — only fire start/done once per phase name and
        # rely on the warmup's own idempotency for the second invocation.
        if phase_name in fired_phases:
            try:
                warmup(ctx)
            except Exception:
                pass
            continue
        fired_phases.add(phase_name)
        if progress is not None:
            progress.on_phase_start(phase_name, None)
        try:
            warmup(ctx)
        except Exception:  # warmup failures must not abort the build
            pass
        if progress is not None:
            done = getattr(progress, "on_phase_done", None)
            if callable(done):
                done(phase_name)
