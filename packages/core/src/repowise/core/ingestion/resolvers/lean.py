"""Lean 4 import resolution (lightweight regex tier).

Lean modules are path-derived rather than declared with a ``module`` head:
``Mathlib/Data/Nat/Basic.lean`` defines ``Mathlib.Data.Nat.Basic``. The index
therefore uses only the path-convention inverse for ``.lean`` files.

Core and common-library roots (``Init``/``Std``/``Mathlib``/...) are dropped
after a local miss. This matches the Haskell resolver's tradeoff: local files
shadow well-known roots, while unresolved standard-library-like imports do not
mint noisy external nodes.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .module_name_index import get_or_build_module_index, lookup_module

if TYPE_CHECKING:
    from .context import ResolverContext

_MODULE_DECL_RE = re.compile(r"(?!x)x")

_STDLIB_PREFIXES = frozenset(
    {"Init", "Std", "Lean", "Mathlib", "Batteries", "Aesop", "Qq"}
)


def _path_to_module(path: str) -> str | None:
    stem = path.rsplit(".", 1)[0]
    segments = [s for s in stem.split("/") if s]
    if not segments:
        return None
    return ".".join(segments)


def _get_index(ctx: ResolverContext) -> dict[str, list[str]]:
    return get_or_build_module_index(
        ctx,
        cache_attr="_lean_module_index",
        extensions=(".lean",),
        declaration_re=_MODULE_DECL_RE,
        path_to_module=_path_to_module,
    )


def resolve_lean_import(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> str | None:
    hit = lookup_module(_get_index(ctx), module_path)
    if hit and hit != importer_path:
        return hit
    if hit == importer_path:
        return None
    if module_path.split(".", 1)[0] in _STDLIB_PREFIXES:
        return None
    return f"external:{module_path}"
