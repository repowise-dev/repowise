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


def _warmup_dotnet(ctx: "ResolverContext") -> None:
    from .resolvers.dotnet import get_or_build_index

    get_or_build_index(ctx)


def _warmup_go(ctx: "ResolverContext") -> None:
    from .resolvers.go_workspace import get_or_build_go_index

    get_or_build_go_index(ctx)


# Map language tag → (phase-event name, warmup function). The phase
# name shows up in the CLI progress bar and in ``state.json`` timings.
_WARMUPS: dict[str, tuple[str, Warmup]] = {
    "csharp": ("graph.dotnet_index", _warmup_dotnet),
    "go": ("graph.go_index", _warmup_go),
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
    for lang, (phase_name, warmup) in _WARMUPS.items():
        if lang not in present_langs:
            continue
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
