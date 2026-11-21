"""COBOL ``COPY`` member resolution against copybook files in the repo.

A member resolves case-insensitively to a COBOL file with that stem, a
copybook extension first, then one under the named library, then the one
nearest the copying file. With no copybook, a lone BMS mapset of that name
(the source of a generated symbolic map) answers. An unresolved member
(``DFHAID``, a vendor or system copybook) becomes an external node, never a guess.
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING

from ..cobol.cics import DYNAMIC_PREFIX
from ..languages.registry import REGISTRY

if TYPE_CHECKING:
    from .context import ResolverContext

# A ``.dcl`` is indexed only when its content reads as a COBOL DCLGEN member.
_COPYBOOK_SUFFIXES = frozenset({".cpy", ".dcl"})


def _is_call_import(ctx: ResolverContext, importer_path: str, module_path: str) -> bool:
    parsed = (ctx.parsed_files or {}).get(importer_path)
    if not parsed:
        return False
    return any(
        imp.module_path == module_path and imp.raw_statement.startswith("CALL")
        for imp in getattr(parsed, "imports", ())
    )


def _resolve_missing_copybook(member: str, ctx: ResolverContext) -> str | None:
    # A symbolic map copybook is generated from the BMS mapset of that name.
    maps = [p for p in ctx.stem_map.get(member.lower(), ()) if p.lower().endswith(".bms")]
    return maps[0] if len(maps) == 1 else ctx.add_external_node(f"copybook:{member}")


def resolve_cobol_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    if module_path.startswith(DYNAMIC_PREFIX):
        # A program chosen at runtime: shown as such, never matched to a file.
        # CALL statements produce a node name without "cics-", while CICS transfers keep "cics-".
        prefix = "" if _is_call_import(ctx, importer_path, module_path) else "cics-"
        return ctx.add_external_node(f"{prefix}{module_path}")
    library, _, member = module_path.rpartition("/")
    spec = REGISTRY.get("cobol")
    extensions = (spec.extensions if spec else frozenset()) | _COPYBOOK_SUFFIXES
    candidates = [
        path
        for path in ctx.stem_map.get(member.lower(), ())
        if posixpath.splitext(path)[1].lower() in extensions and path != importer_path
    ]
    if not candidates:
        return _resolve_missing_copybook(member, ctx)
    importer_dir = posixpath.dirname(importer_path).split("/")
    order = {path: index for index, path in enumerate(candidates)}

    def rank(path: str) -> tuple[int, int, int, int]:
        parts = path.split("/")
        shared = 0
        for a, b in zip(parts[:-1], importer_dir, strict=False):
            if a != b:
                break
            shared += 1
        in_library = bool(library) and library.lower() in (p.lower() for p in parts[:-1])
        is_copybook = posixpath.splitext(path)[1].lower() in _COPYBOOK_SUFFIXES
        return (not is_copybook, not in_library, -shared, order[path])

    return min(candidates, key=rank)
