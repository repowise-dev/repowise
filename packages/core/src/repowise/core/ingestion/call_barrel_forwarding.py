"""Barrel re-export forwarding mixed into ``CallResolver``.

Records, for each file that re-exports names it does not declare, the file
that declares each one, following chains of barrels to their origin.
"""

from __future__ import annotations

from collections import defaultdict

from .models import Import, NamedBinding


def _flattened_wildcard_source(imp: Import) -> str | None:
    """The repository file whose every name *imp* forwards, or None.

    Two shapes qualify: Rust ``pub use foo::*`` (``is_reexport``) and
    Python/JS ``from foo import *`` (a "*" imported name).
    """
    is_wildcard = imp.is_reexport or "*" in imp.imported_names
    if not is_wildcard or not imp.resolved_file:
        return None
    if imp.resolved_file.startswith("external:"):
        return None
    # ``export * as ns from "x"`` forwards the module under ``ns``,
    # so x's names are reachable as ``ns.name`` and are NOT this
    # file's own exports. Flattening them makes a bare ``name``
    # resolve into a nested namespace it was never in.
    if any(b.local_name == "*" and b.exported_name for b in imp.bindings):
        return None
    return imp.resolved_file


def _renames_on_the_way(binding: NamedBinding | None, name: str) -> bool:
    return binding is not None and (binding.exported_name or name) != name


class BarrelForwardingMixin:
    """Barrel origin maps, built once at construction."""

    def _follow_barrel_exports(self) -> None:
        """Detect barrel/re-export files and record origin mappings.

        A barrel file imports a name and re-exports it without defining it
        locally (e.g., ``__init__.py`` with ``from .calculator import Calculator``).
        When downstream code imports from the barrel, we follow chains to
        find the actual defining file.
        """
        self._record_direct_barrel_origins()
        wildcard_sources = self._record_wildcard_barrel_origins()
        self._forward_barrels_over_barrels(wildcard_sources)
        self._deepen_barrel_chains()

    def _record_direct_barrel_origins(self) -> None:
        for path, name_to_file in self._import_names.items():
            file_syms = self._file_symbols.get(path, {})
            for name, source_file in name_to_file.items():
                # An origin outside the repo names no file a reader of this
                # map can look up; the wildcard pass refuses one too.
                if name not in file_syms and not source_file.startswith("external:"):
                    self._barrel_origins[path][name] = source_file

    def _record_wildcard_barrel_origins(self) -> dict[str, list[str]]:
        """Forward every name a wildcard import publishes; return each file's sources.

        This is how package ``__init__.py`` barrels commonly re-export a
        subpackage. ``build_import_name_maps`` skips the "*" name (it is not a
        binding), so without this pass the barrel chain dead-ends one hop short
        of the real definition and a call through the barrel resolves to nothing.
        """
        wildcard_sources: dict[str, list[str]] = defaultdict(list)
        for path, parsed in self._parsed_files.items():
            file_syms = self._file_symbols.get(path, {})
            for imp in parsed.imports:
                resolved = _flattened_wildcard_source(imp)
                if resolved is None:
                    continue
                if resolved != path:
                    wildcard_sources[path].append(resolved)
                self._forward_published_names(path, resolved, file_syms)
        return wildcard_sources

    def _forward_published_names(
        self, path: str, resolved: str, file_syms: dict[str, str]
    ) -> None:
        source_syms = self._file_symbols.get(resolved, {})
        source_parsed = self._parsed_files.get(resolved)
        published = (
            (*source_syms, *source_parsed.export_aliases)
            if source_parsed
            else tuple(source_syms)
        )
        for sym_name in published:
            if sym_name not in file_syms:
                self._barrel_origins[path][sym_name] = resolved

    def _forward_barrels_over_barrels(self, wildcard_sources: dict[str, list[str]]) -> None:
        """Forward what each wildcard source re-exports too, to a fixpoint.

        The wildcard pass forwards only what the source file DECLARES, so a
        barrel over a barrel forwards nothing and the chain breaks at its
        first link rather than its last. The multi-hop pass cannot do this
        job: it deepens entries that exist, and here none do.

        Sorted so that a name two of this file's barrels both forward lands on
        the same origin whatever order the repository was walked in.
        """
        for _ in range(4):
            changed = False
            for path, sources in sorted(wildcard_sources.items()):
                if self._forward_source_origins(path, sources):
                    changed = True
            if not changed:
                break

    def _forward_source_origins(self, path: str, sources: list[str]) -> bool:
        file_syms = self._file_symbols.get(path, {})
        origins = self._barrel_origins[path]
        changed = False
        for source in sorted(sources):
            source_bindings = self._import_bindings.get(source, {})
            for name, declaring in sorted(self._barrel_origins.get(source, {}).items()):
                if name in file_syms or name in origins or declaring == path:
                    continue
                # The map records only the declaring file, not the name the
                # symbol has there, so a renaming hop could forward a key that
                # means something else at the far end. Refuse it.
                if _renames_on_the_way(source_bindings.get(name), name):
                    continue
                origins[name] = declaring
                changed = True
        return changed

    def _deepen_barrel_chains(self) -> None:
        """Multi-hop: follow chains up to 5 hops."""
        for _ in range(4):
            changed = False
            for _path, origins in list(self._barrel_origins.items()):
                if self._deepen_origins(origins):
                    changed = True
            if not changed:
                break

    def _deepen_origins(self, origins: dict[str, str]) -> bool:
        changed = False
        for name, source in list(origins.items()):
            deeper = self._barrel_origins.get(source, {}).get(name)
            if deeper and deeper != source:
                origins[name] = deeper
                changed = True
        return changed
