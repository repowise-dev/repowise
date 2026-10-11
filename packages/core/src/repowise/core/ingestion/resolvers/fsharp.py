"""F# import resolution (lightweight regex tier).

``open Foo.Bar`` names a namespace or a top-level module. Namespaces span
many files, so resolution is deliberately conservative:

1. Declared-name index — file-level ``namespace Foo.Bar`` /
   ``module Foo.Bar`` headers (nested ``module X =`` bindings are
   excluded by the no-``=`` guard). An ``open`` resolves only when
   exactly ONE other file declares the name — an ambiguous namespace
   produces no edge rather than a guessed one. Trailing segments strip
   progressively (``open Foo.Bar.Helpers`` hits the file-level module
   ``Foo.Bar`` when ``Helpers`` is nested inside it).
2. ``System.* / Microsoft.* / FSharp.*`` → dropped after a local miss.
3. Everything else → external node.

The real F# dependency spine — fsproj ``<Compile Include>`` order — is
emitted separately by the compile-order graph pass.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .module_name_index import get_or_build_module_index

if TYPE_CHECKING:
    from .context import ResolverContext

# File-level declarations only: a nested module binding always carries
# `= …` on the same line; `module rec` / access modifiers allowed.
_DECL_RE = re.compile(
    r"^(?:namespace[ \t]+(?:rec[ \t]+)?|"
    r"module[ \t]+(?:rec[ \t]+)?(?:public[ \t]+|private[ \t]+|internal[ \t]+)?)"
    r"([A-Z][A-Za-z0-9_.]*)[ \t]*\r?$",
    re.M,
)

_DOTNET_PREFIXES = frozenset({"System", "Microsoft", "FSharp"})


def _get_index(ctx: ResolverContext) -> dict[str, list[str]]:
    return get_or_build_module_index(
        ctx,
        cache_attr="_fsharp_module_index",
        extensions=(".fs", ".fsx"),
        declaration_re=_DECL_RE,
        path_to_module=None,
    )


def resolve_fsharp_import(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> str | None:
    index = _get_index(ctx)
    candidate = module_path
    while candidate:
        declarers = [p for p in index.get(candidate, ()) if p != importer_path]
        if declarers:
            # exactly one declaring file = unambiguous; a namespace spanning
            # several files yields no edge (no guessing)
            return declarers[0] if len(declarers) == 1 else None
        if "." not in candidate:
            break
        candidate = candidate.rsplit(".", 1)[0]
    if module_path.split(".", 1)[0] in _DOTNET_PREFIXES:
        return None
    return f"external:{module_path}"
