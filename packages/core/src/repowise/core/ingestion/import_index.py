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


def build_import_name_maps(parsed_files: dict[str, ParsedFile]) -> ImportNameMaps:
    """Build the shared import-name maps from every file's resolved imports.

    Mirrors the historical ``CallResolver._build_indices`` import loop,
    including the ``binding.source_file`` back-fill side effect that
    downstream barrel-origin chasing relies on.
    """
    maps = ImportNameMaps()
    for path, parsed in parsed_files.items():
        for imp in parsed.imports:
            if imp.resolved_file is None:
                continue
            resolved = imp.resolved_file
            if imp.bindings:
                for binding in imp.bindings:
                    if binding.local_name == "*":
                        continue
                    binding.source_file = resolved
                    maps.import_names[path][binding.local_name] = resolved
                    maps.import_bindings[path][binding.local_name] = binding
                    if binding.is_module_alias:
                        maps.module_aliases[path][binding.local_name] = resolved
            else:
                # Fallback for imports without binding data
                for name in imp.imported_names:
                    if name != "*":
                        maps.import_names[path][name] = resolved
    return maps
