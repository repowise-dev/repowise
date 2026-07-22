"""Back-compat shim: the scope resolver now lives in ``repowise.core``.

The pure scope-resolution logic moved to :mod:`repowise.core.generation.scope`
so the OSS server and hosted can resolve a generation scope the same way the CLI
does, without depending on the CLI package. This module re-exports it unchanged
for existing call sites and tests.
"""

from __future__ import annotations

from repowise.core.generation.scope import (
    ScopePlan,
    _layer_membership,
    _ranked_ids_to_seed,
    _selection_inputs,
    build_cost_plans,
    build_dependencies,
    build_ranked_seed,
    load_page_records,
    resolve_scope,
    selection_page_ids,
)

__all__ = [
    "ScopePlan",
    "_layer_membership",
    "_ranked_ids_to_seed",
    "_selection_inputs",
    "build_cost_plans",
    "build_dependencies",
    "build_ranked_seed",
    "load_page_records",
    "resolve_scope",
    "selection_page_ids",
]
