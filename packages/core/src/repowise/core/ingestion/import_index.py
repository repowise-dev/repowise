"""Shared import-name index construction for the call and heritage resolvers.

Both resolvers used to derive the same ``{file → {local_name → source_file}}``
mapping independently from every file's imports. Build it once here and
inject it into both (the GraphBuilder does this during ``build()``); the
resolvers keep a backwards-compatible fallback that builds the maps
themselves when none are supplied.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .models import NamedBinding, ParsedFile


@dataclass(slots=True)
class ImportNameMaps:
    """Per-file import-name lookups shared across resolution passes.

    ``import_names``    — {file: {local_name: source_file}}
    ``import_bindings`` — {file: {local_name: NamedBinding}} (call resolver only)
    ``module_aliases``  — {file: {alias: source_file}} (call resolver only)
    ``local_scopes``    — {file: {local_name: function line spans}} for a name
                          only imports inside function bodies bind (Python)
    """

    import_names: dict[str, dict[str, str]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    import_bindings: dict[str, dict[str, NamedBinding]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    module_aliases: dict[str, dict[str, str]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    local_scopes: dict[str, dict[str, tuple[tuple[int, int], ...]]] = field(
        default_factory=lambda: defaultdict(dict)
    )


def build_import_name_maps(parsed_files: dict[str, ParsedFile]) -> ImportNameMaps:
    """Build the shared import-name maps from every file's resolved imports.

    Mirrors the historical ``CallResolver._build_indices`` import loop,
    including the ``binding.source_file`` back-fill side effect that
    downstream barrel-origin chasing relies on.
    """
    maps = ImportNameMaps()
    for path, parsed in parsed_files.items():
        file_wide: set[str] = set()
        for imp in parsed.imports:
            if imp.resolved_file is None:
                continue
            resolved = imp.resolved_file
            if imp.bindings:
                for binding in imp.bindings:
                    if binding.local_name == "*":
                        continue
                    # A binding whose source_file was already pinned to a
                    # submodule file (``from pkg import submodule`` fan-out,
                    # #1193) must keep it: ``imp.resolved_file`` is the package
                    # ``__init__.py``, which declares nothing the submodule
                    # name resolves to. Only back-fill when unset.
                    if binding.source_file is None:
                        binding.source_file = resolved
                    maps.import_names[path][binding.local_name] = binding.source_file
                    maps.import_bindings[path][binding.local_name] = binding
                    if binding.is_module_alias:
                        maps.module_aliases[path][binding.local_name] = binding.source_file
                    _record_scope(maps, path, binding.local_name, imp.scopes, file_wide)
            else:
                # Fallback for imports without binding data
                for name in imp.imported_names:
                    if name != "*":
                        maps.import_names[path][name] = resolved
    return maps


def _record_scope(
    maps: ImportNameMaps,
    path: str,
    name: str,
    scopes: tuple[tuple[int, int], ...] | None,
    file_wide: set[str],
) -> None:
    """Track the function bodies *name* is bound in, until a file-wide import binds it."""
    if scopes is None:
        file_wide.add(name)
        if path in maps.local_scopes:
            maps.local_scopes[path].pop(name, None)
    elif name not in file_wide:
        maps.local_scopes[path][name] = maps.local_scopes[path].get(name, ()) + scopes
