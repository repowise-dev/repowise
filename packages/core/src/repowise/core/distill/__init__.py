"""Distill — index-aware compaction of noisy command output.

This package `__init__` is a **lazy re-export shim** and imports nothing at
module scope. The public names below resolve on first attribute access via
PEP 562 ``__getattr__``, so ``from repowise.core.distill import
distill_output`` still works and costs the same as before, while importing a
sibling submodule costs only that submodule.

Why it matters: a package ``__init__`` runs on *any* submodule import, so the
engine's graph (structlog, the filter registry, the omission store) used to be
charged to ``import repowise.core.distill.skeleton`` — 556ms, paid by the
PostToolUse Read hook on every fire that reached the skeleton path. Same bug
class as ``search.py``'s ``persistence.sql`` and ``claude_config.py``'s
``core.workspace.config``; this is the shape that stops it recurring here.

The engine itself lives in :mod:`repowise.core.distill.engine`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from repowise.core.distill.budget import estimate_tokens, savings_pct
    from repowise.core.distill.engine import DistillResult, distill_output
    from repowise.core.distill.markers import parse_marker_refs, render_marker
    from repowise.core.distill.registry import filter_registry
    from repowise.core.distill.router import normalize_command, select_filter
    from repowise.core.distill.store import OmissionStore

__all__ = [
    "DistillResult",
    "OmissionStore",
    "distill_output",
    "estimate_tokens",
    "filter_registry",
    "normalize_command",
    "parse_marker_refs",
    "render_marker",
    "savings_pct",
    "select_filter",
]

#: Public name -> defining submodule. The single source of truth for both
#: ``__getattr__`` and ``__dir__``; ``__all__`` above is asserted against it.
_EXPORTS: dict[str, str] = {
    "DistillResult": "engine",
    "OmissionStore": "store",
    "distill_output": "engine",
    "estimate_tokens": "budget",
    "filter_registry": "registry",
    "normalize_command": "router",
    "parse_marker_refs": "markers",
    "render_marker": "markers",
    "savings_pct": "budget",
    "select_filter": "router",
}


def __getattr__(name: str) -> Any:
    """Resolve a public export on first access, importing only its module."""
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(f"{__name__}.{module}"), name)
    globals()[name] = value  # cache: subsequent accesses skip __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
