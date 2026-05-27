"""Central language registry — single source of truth.

Every language-specific constant previously scattered across models.py,
parser.py, language_data.py, traverser.py, page_generator.py, cli/ui.py,
git_indexer.py, and others is consolidated here.

This module is a **leaf dependency** — it imports nothing from the
ingestion pipeline (no parser, graph, traverser, etc.) to avoid circular
imports.

Frontend language colours are maintained in parallel in
``packages/web/src/lib/utils/confidence.ts`` and
``packages/web/src/components/``.  A Phase 2 build task will generate
the TypeScript file from this registry.
"""

from __future__ import annotations

from collections.abc import Iterable

from .spec import LanguageSpec
from .specs import ALL_SPECS as _SPECS

# =========================================================================
# LanguageRegistry
# =========================================================================


class LanguageRegistry:
    """Central registry.  All language-specific lookups go through here.

    Instantiated once at module level as ``REGISTRY``.  The registry is
    immutable after construction — all data comes from ``_SPECS``.
    """

    __slots__ = ("_ext_map", "_filename_map", "_specs")

    def __init__(self, specs: tuple[LanguageSpec, ...] = _SPECS) -> None:
        self._specs: dict[str, LanguageSpec] = {s.tag: s for s in specs}

        # Build extension → tag map (first spec wins if extensions overlap)
        self._ext_map: dict[str, str] = {}
        for spec in specs:
            for ext in spec.extensions:
                if ext not in self._ext_map:
                    self._ext_map[ext] = spec.tag

        # Build special filename → tag map
        self._filename_map: dict[str, str] = {}
        for spec in specs:
            for fn in spec.special_filenames:
                if fn not in self._filename_map:
                    self._filename_map[fn] = spec.tag

    # -- Single-spec lookups ---------------------------------------------

    def get(self, tag: str) -> LanguageSpec | None:
        """Return the spec for a language tag, or None."""
        return self._specs.get(tag)

    def from_extension(self, ext: str) -> str:
        """Return the language tag for a file extension, or ``'unknown'``."""
        return self._ext_map.get(ext, "unknown")

    def from_filename(self, name: str) -> str | None:
        """Return the language tag for a special filename, or None."""
        return self._filename_map.get(name)

    # -- Aggregated lookups ----------------------------------------------

    def all_extensions(self) -> dict[str, str]:
        """Return ``{ext: tag}`` for all registered extensions."""
        return dict(self._ext_map)

    def all_special_filenames(self) -> dict[str, str]:
        """Return ``{filename: tag}`` for all special filenames."""
        return dict(self._filename_map)

    def all_code_extensions(self) -> frozenset[str]:
        """Return extensions for all ``is_code=True`` languages."""
        return frozenset(
            ext for spec in self._specs.values() if spec.is_code for ext in spec.extensions
        )

    def code_languages(self) -> frozenset[str]:
        """Return tags for code languages (not config/markup/data)."""
        return frozenset(s.tag for s in self._specs.values() if s.is_code and not s.is_passthrough)

    def config_languages(self) -> frozenset[str]:
        """Return tags for non-code languages (config/markup/data)."""
        return frozenset(s.tag for s in self._specs.values() if not s.is_code)

    def passthrough_languages(self) -> frozenset[str]:
        """Return tags for languages with no AST parser."""
        return frozenset(s.tag for s in self._specs.values() if s.is_passthrough)

    def infra_languages(self) -> frozenset[str]:
        """Return tags for infrastructure languages."""
        return frozenset(s.tag for s in self._specs.values() if s.is_infra)

    def entry_point_names(self) -> frozenset[str]:
        """Return the union of all entry-point filename patterns."""
        return frozenset(p for s in self._specs.values() for p in s.entry_point_patterns)

    def manifest_filenames(self) -> frozenset[str]:
        """Return the union of all manifest filenames."""
        return frozenset(f for s in self._specs.values() for f in s.manifest_files)

    def blocked_dirs(self) -> frozenset[str]:
        """Return the union of all per-language blocked directories."""
        return frozenset(d for s in self._specs.values() for d in s.blocked_dirs)

    def generated_suffixes(self) -> frozenset[str]:
        """Return the union of all generated-file suffixes."""
        return frozenset(sf for s in self._specs.values() for sf in s.generated_suffixes)

    def extensions_for(self, tags: Iterable[str]) -> frozenset[str]:
        """Return extensions for a specific set of language tags."""
        tag_set = set(tags)
        return frozenset(
            ext for spec in self._specs.values() if spec.tag in tag_set for ext in spec.extensions
        )

    def all_specs(self) -> list[LanguageSpec]:
        """Return all registered specs."""
        return list(self._specs.values())


# Module-level singleton
REGISTRY = LanguageRegistry()
