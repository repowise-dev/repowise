"""Perl ``use`` / ``require`` import resolution.

Handled forms:

1. ``use Foo::Bar;`` / ``require Foo::Bar;`` — the module maps to
   ``Foo/Bar.pm`` and is matched against repo paths by suffix, as ``@INC``
   would find it under any ``lib/`` (or other) root. One match links; on a tie
   the candidate sharing the longest directory prefix with the importer wins,
   and a tie there links nothing.
2. ``require "lib/x.pl";`` — a file path, resolved against the importing
   file's directory, then the repo root, then by unique multi-segment suffix.
3. Pragmas (all-lowercase names: ``strict``, ``warnings``, ``parent``…)
   produce no node. Anything else not in the repo (core and CPAN modules,
   interpolated paths) becomes an external node.
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import ResolverContext

_PRAGMA = re.compile(r"^[a-z][a-z0-9_]*$")
_MODULE = re.compile(r"^[A-Za-z_]\w*(?:::\w+)*$")


def resolve_perl_import(
    module_path: str,
    importer_path: str,
    ctx: ResolverContext,
) -> str | None:
    """Resolve a ``use`` / ``require`` argument to a repo-relative file path."""
    raw = module_path.strip()
    if not raw:
        return None
    if _PRAGMA.match(raw):
        return None
    if _MODULE.match(raw):
        return _resolve_module(raw, importer_path, ctx) or ctx.add_external_node(raw)
    if "$" in raw or "@" in raw or raw.startswith(("/", "~")):
        return ctx.add_external_node(raw)
    return _resolve_file(raw, importer_path, ctx) or ctx.add_external_node(raw)


def _resolve_module(module: str, importer_path: str, ctx: ResolverContext) -> str | None:
    rel = module.replace("::", "/") + ".pm"
    needle = f"/{rel}"
    hits = [p for p in ctx.sorted_paths if p == rel or p.endswith(needle)]
    if len(hits) <= 1:
        return hits[0] if hits else None
    importer_dirs = posixpath.dirname(importer_path).split("/")
    scored = sorted(
        ((_shared_segments(importer_dirs, posixpath.dirname(h).split("/")), h) for h in hits),
        reverse=True,
    )
    if scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


def _shared_segments(a: list[str], b: list[str]) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


def _resolve_file(path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    importer_dir = posixpath.dirname(importer_path)
    for base in (importer_dir, ""):
        cand = posixpath.normpath(posixpath.join(base, path))
        if cand in ctx.path_set:
            return cand
    if "/" not in path:
        return None
    needle = f"/{path}"
    hits = [p for p in ctx.sorted_paths if p.endswith(needle)]
    return hits[0] if len(hits) == 1 else None
