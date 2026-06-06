"""Haskell import resolution (lightweight regex tier).

Resolution order:

1. Declared-module index — ``module Foo.Bar`` scanned from every
   ``.hs``/``.lhs`` file. The inverse fallback derives the module name
   from the *trailing capitalized path segments*
   (``src/Data/Aeson/Types.hs`` → ``Data.Aeson.Types``), which handles
   arbitrary hs-source-dirs without parsing ``.cabal``/``package.yaml``
   — Haskell's PascalCase directory convention IS the source-dir marker.
2. base-ish namespaces (``Prelude``/``Data.*``/``Control.*``/…) →
   dropped after a local miss. This consciously also drops external
   *packages* living under those namespaces (e.g. ``Data.Aeson`` used
   from another repo) rather than minting externals — same trade as the
   JVM ``java.*`` filter: no noise edges, no false internals.
3. Everything else → external node.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .module_name_index import get_or_build_module_index, lookup_module

if TYPE_CHECKING:
    from .context import ResolverContext

_MODULE_DECL_RE = re.compile(r"^module[ \t]+([A-Z][A-Za-z0-9_.']*)", re.M)

_BASE_PREFIXES = frozenset(
    {
        "Prelude", "Control", "Data", "System", "GHC", "Text", "Foreign",
        "Numeric", "Debug", "Unsafe", "Type",
    }
)


def _path_to_module(path: str) -> str | None:
    segments = path.rsplit(".", 1)[0].split("/")
    tail: list[str] = []
    for seg in reversed(segments):
        if seg and seg[0].isupper():
            tail.append(seg)
        else:
            break
    if not tail:
        return None
    return ".".join(reversed(tail))


def _get_index(ctx: ResolverContext) -> dict[str, list[str]]:
    return get_or_build_module_index(
        ctx,
        cache_attr="_haskell_module_index",
        extensions=(".hs", ".lhs"),
        declaration_re=_MODULE_DECL_RE,
        path_to_module=_path_to_module,
    )


def resolve_haskell_import(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> str | None:
    hit = lookup_module(_get_index(ctx), module_path)
    if hit and hit != importer_path:
        return hit
    if hit == importer_path:
        return None
    if module_path.split(".", 1)[0] in _BASE_PREFIXES:
        return None
    return f"external:{module_path}"
