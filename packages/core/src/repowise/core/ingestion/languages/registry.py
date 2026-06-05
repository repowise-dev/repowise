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

import re
from collections.abc import Iterable

from .spec import LanguageSpec
from .specs import ALL_SPECS as _SPECS

# Entry-point filename stems that are conventional across languages rather
# than owned by any one of them ("bootstrap.php", "entry.ts", "cli.py",
# "server.go", "main.rs", "app.py", "index.js" …). Language-unique stems
# (manage/wsgi/asgi/__main__ → python, mod → rust) live on their specs.
_GENERIC_ENTRY_STEMS: frozenset[str] = frozenset(
    {"main", "index", "app", "server", "cli", "bootstrap", "entry"}
)

# Stems the traverser's is_entry_point *flag* accepts for any language —
# deliberately a different (tighter on cli/bootstrap/entry, looser on
# run/start) set than the tour-bonus stems above: the flag is strong
# evidence, the tour stem a weak bonus. Per-language flag stems
# (wsgi/asgi → python) live on the specs.
_GENERIC_ENTRY_FLAG_STEMS: frozenset[str] = frozenset(
    {"main", "index", "app", "run", "server", "start"}
)

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

    def non_infra_code_extensions(self) -> frozenset[str]:
        """Extensions of code languages that are not infra (typing guard).

        Shell/terraform are code *and* infra — their files may still be
        promoted to infra/CI presentation types by name and path; genuine
        source extensions (.py … .dart .hs .clj) never are.
        """
        return frozenset(
            ext
            for spec in self._specs.values()
            if spec.is_code and not spec.is_infra
            for ext in spec.extensions
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

    # -- Knowledge-graph capability lookups --------------------------------

    def import_support_map(self) -> dict[str, str]:
        """Return ``{tag: "full" | "partial" | "none"}`` for every language."""
        return {s.tag: s.import_support for s in self._specs.values()}

    def import_support_for(self, tag: str) -> str:
        """Return the import-support tier for *tag* (``"none"`` if unknown)."""
        spec = self._specs.get(tag)
        return spec.import_support if spec else "none"

    def entry_filename_stems(self) -> frozenset[str]:
        """Generic + per-language entry-point filename stems (tour bonus set)."""
        return _GENERIC_ENTRY_STEMS | frozenset(
            stem for s in self._specs.values() for stem in s.entry_stems
        )

    def entry_flag_stems(self) -> frozenset[str]:
        """Generic + per-language stems for the traverser's is_entry_point flag."""
        return _GENERIC_ENTRY_FLAG_STEMS | frozenset(
            stem for s in self._specs.values() for stem in s.entry_flag_stems
        )

    def test_stem_prefixes(self) -> tuple[str, ...]:
        """Union of test filename-stem prefixes, sorted for determinism."""
        return tuple(sorted({p for s in self._specs.values() for p in s.test_stem_prefixes}))

    def test_stem_suffixes(self) -> tuple[str, ...]:
        """Union of test filename-stem suffixes, sorted for determinism."""
        return tuple(sorted({p for s in self._specs.values() for p in s.test_stem_suffixes}))

    def test_infixes(self) -> tuple[str, ...]:
        """Union of test filename infixes, sorted for determinism."""
        return tuple(sorted({p for s in self._specs.values() for p in s.test_infixes}))

    def test_fixture_stems(self) -> frozenset[str]:
        """Union of test-fixture filename stems (conftest, spec_helper, …)."""
        return frozenset(p for s in self._specs.values() for p in s.test_fixture_stems)

    def suite_anchor_stems(self) -> frozenset[str]:
        """Union of test-suite anchor stems for the tour's closing stop."""
        return frozenset(p for s in self._specs.values() for p in s.suite_anchor_stems)

    def descriptor_filenames(self) -> frozenset[str]:
        """Union of declaration-descriptor filenames (module-info.java, …)."""
        return frozenset(f for s in self._specs.values() for f in s.descriptor_filenames)

    def camel_test_res_by_extension(self) -> dict[str, re.Pattern[str]]:
        """Per-extension case-sensitive camel-boundary test-suffix regexes.

        Each language's ``test_camel_suffixes`` compile to one anchored
        pattern (``(?<=[a-z0-9])(?:Tests|Test|IT)$``) keyed by that
        language's own extensions — the lowercase-boundary lookbehind is what
        keeps ``latest.java``/``contest.cs`` and bare ``Test.java`` out.
        """
        result: dict[str, re.Pattern[str]] = {}
        for spec in self._specs.values():
            if not spec.test_camel_suffixes:
                continue
            # Longest-first so "Tests" wins over "Test" inside the alternation.
            alternation = "|".join(
                sorted(spec.test_camel_suffixes, key=lambda sfx: (-len(sfx), sfx))
            )
            pattern = re.compile(rf"(?<=[a-z0-9])(?:{alternation})$")
            for ext in spec.extensions:
                result.setdefault(ext, pattern)
        return result

    def camel_fixture_res_by_extension(self) -> dict[str, re.Pattern[str]]:
        """Per-extension case-sensitive camel-boundary fixture-suffix regexes.

        Same compilation rules as :meth:`camel_test_res_by_extension`, for
        ``fixture_camel_suffixes`` — files holding test support data
        (``FooFixtures.java``) rather than tests.
        """
        result: dict[str, re.Pattern[str]] = {}
        for spec in self._specs.values():
            if not spec.fixture_camel_suffixes:
                continue
            alternation = "|".join(
                sorted(spec.fixture_camel_suffixes, key=lambda sfx: (-len(sfx), sfx))
            )
            pattern = re.compile(rf"(?<=[a-z0-9])(?:{alternation})$")
            for ext in spec.extensions:
                result.setdefault(ext, pattern)
        return result

    def test_dir_paths(self) -> tuple[str, ...]:
        """Union of multi-segment test-root dir paths, sorted for determinism."""
        return tuple(sorted({p for s in self._specs.values() for p in s.test_dir_paths}))

    def test_dir_tokens_by_language(self) -> dict[str, frozenset[str]]:
        """Per-language single-segment test-dir tokens (unambiguous for
        that language's files — ruby's ``spec/``)."""
        return {
            s.tag: frozenset(s.test_dir_tokens)
            for s in self._specs.values()
            if s.test_dir_tokens
        }

    def test_dir_suffixes(self) -> tuple[str, ...]:
        """Union of case-sensitive test-project dir suffixes, sorted."""
        return tuple(sorted({p for s in self._specs.values() for p in s.test_dir_suffixes}))

    def layer_dir_hints_by_language(self) -> dict[str, tuple[tuple[str, str], ...]]:
        """Per-language (dir_hint, layer_name) hints, sorted for determinism.

        Keys are language tags; hints apply only to that language's files —
        never another's. See ``LanguageSpec.layer_dir_hints`` for the shapes.
        """
        return {
            s.tag: tuple(sorted(s.layer_dir_hints))
            for s in self._specs.values()
            if s.layer_dir_hints
        }

    def all_specs(self) -> list[LanguageSpec]:
        """Return all registered specs."""
        return list(self._specs.values())


# Module-level singleton
REGISTRY = LanguageRegistry()
