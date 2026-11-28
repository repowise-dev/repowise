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


def resolve_cobol_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    if module_path.startswith(DYNAMIC_PREFIX):
        # A program chosen at runtime: shown as such, never matched to a file.
        return ctx.add_external_node(f"cics-{module_path}")
    library, _, member = module_path.rpartition("/")
    spec = REGISTRY.get("cobol")
    extensions = (spec.extensions if spec else frozenset()) | _COPYBOOK_SUFFIXES
    candidates = [
        path
        for path in ctx.stem_map.get(member.lower(), ())
        if posixpath.splitext(path)[1].lower() in extensions and path != importer_path
    ]
    if not candidates:
        # A symbolic map copybook is generated from the BMS mapset of that name.
        maps = [p for p in ctx.stem_map.get(member.lower(), ()) if p.lower().endswith(".bms")]
        return maps[0] if len(maps) == 1 else ctx.add_external_node(f"copybook:{member}")
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
