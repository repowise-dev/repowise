"""Language capability registry — parity goldens and drift manifests.

Phase 0 of the cross-language KG accuracy work moved per-language knowledge
(test filename conventions, entry stems, import-support tiers) onto the
``LanguageSpec`` registry and switched ``generation/layers.py`` and
``generation/tour.py`` to registry derivations.

Two kinds of test live here:

1. **Parity goldens** — the derived unions must equal the historical
   hard-coded literals exactly. Phase 0 is a behavior-preserving refactor;
   any spec edit that changes a union must update the golden *consciously*.

2. **Drift manifests** — where the registry knows MORE than the pipeline's
   frozen literals (``_CODE_SUFFIXES``, ``_NON_CODE_LANGUAGES``), the exact
   delta is pinned here. These deltas are the reviewed change-list for the
   Phase 1 switch-over; a failure means the gap moved and the manifest (and
   Phase 1 plan) must be updated together.
"""

from __future__ import annotations

from repowise.core.analysis.kg_curation import _CODE_SUFFIXES
from repowise.core.generation import layers
from repowise.core.generation.tour import _ENTRY_FILENAME_STEMS, _NON_CODE_LANGUAGES
from repowise.core.ingestion.languages.registry import REGISTRY


# ---------------------------------------------------------------------------
# Parity goldens — derived unions == historical literals
# ---------------------------------------------------------------------------


class TestParityGoldens:
    def test_entry_filename_stems_match_historical_set(self) -> None:
        assert _ENTRY_FILENAME_STEMS == frozenset(
            {
                "index",
                "main",
                "app",
                "server",
                "mod",
                "manage",
                "wsgi",
                "asgi",
                "cli",
                "__main__",
                "bootstrap",
                "entry",
            }
        )

    def test_test_stem_prefixes_match_historical_set(self) -> None:
        assert set(layers._TEST_FILE_STEM_PREFIXES) == {"test_"}

    def test_test_stem_suffixes_match_historical_set(self) -> None:
        # Phase 1.1 added "_unittest" (C/C++ GoogleTest convention) to the
        # historical {"_test", "_spec"} — a conscious Phase 1 change.
        assert set(layers._TEST_FILE_STEM_SUFFIXES) == {"_test", "_spec", "_unittest"}

    def test_test_infixes_match_historical_set(self) -> None:
        assert set(layers._TEST_FILE_INFIXES) == {".test.", ".spec."}

    def test_test_fixture_stems_match_historical_set(self) -> None:
        assert layers._TEST_FIXTURE_STEMS == frozenset(
            {"conftest", "spec_helper", "test_helper"}
        )

    def test_suite_anchor_stems(self) -> None:
        # Only conftest today — kg_curation's closing-stop search is still
        # hard-coded to "conftest"; the consumer flip is Phase 1 work.
        assert REGISTRY.suite_anchor_stems() == frozenset({"conftest"})

    def test_layer_dir_hints_empty_in_phase0(self) -> None:
        # Per-language layer hints land in Phase 1 with per-repo review.
        assert REGISTRY.layer_dir_hints() == ()

    def test_camel_suffix_extension_map(self) -> None:
        # Phase 1.1: case-sensitive camel-boundary test suffixes per language.
        camel = REGISTRY.camel_test_res_by_extension()
        assert set(camel) == {
            ".java", ".kt", ".kts", ".scala", ".cs", ".swift", ".php",
            ".hs", ".lhs",
        }
        assert camel[".java"].pattern == r"(?<=[a-z0-9])(?:Tests|Test|IT)$"
        assert camel[".scala"].pattern == r"(?<=[a-z0-9])(?:Suite|Spec|Test)$"

    def test_test_dir_paths_union(self) -> None:
        assert REGISTRY.test_dir_paths() == (
            "src/integrationtest/java",
            "src/it/java",
            "src/it/scala",
            "src/test/java",
            "src/test/kotlin",
            "src/test/scala",
        )

    def test_test_dir_suffixes_union(self) -> None:
        assert REGISTRY.test_dir_suffixes() == (".Tests",)


# ---------------------------------------------------------------------------
# Import-support tiers
# ---------------------------------------------------------------------------

_FULL = {
    "c",
    "cpp",
    "csharp",
    "go",
    "java",
    "javascript",
    "kotlin",
    "python",
    "ruby",
    "rust",
    "typescript",
}
_PARTIAL = {"luau", "php", "scala", "swift"}


class TestImportSupportTiers:
    def test_every_spec_declares_a_valid_tier(self) -> None:
        for spec in REGISTRY.all_specs():
            assert spec.import_support in {"full", "partial", "none"}, spec.tag

    def test_full_tier_membership(self) -> None:
        support = REGISTRY.import_support_map()
        assert {t for t, v in support.items() if v == "full"} == _FULL

    def test_partial_tier_membership(self) -> None:
        support = REGISTRY.import_support_map()
        assert {t for t, v in support.items() if v == "partial"} == _PARTIAL

    def test_unknown_language_reports_none(self) -> None:
        assert REGISTRY.import_support_for("klingon") == "none"


# ---------------------------------------------------------------------------
# Drift manifests — the registry-vs-pipeline gap, pinned exactly.
# These sets ARE the Phase 1 change list. Update them only together with a
# conscious decision (see prompts/kg-language-accuracy-DECISIONS.md D-011).
# ---------------------------------------------------------------------------

# Code extensions the registry knows but kg_curation._CODE_SUFFIXES doesn't:
# files with these suffixes can currently be mistyped as infra/CI by
# _enrich_type when their names look infra-ish.
_CODE_SUFFIXES_MISSING = {
    ".bash", ".clj", ".cljc", ".cljs", ".cr", ".cts", ".cxx", ".d", ".dart",
    ".elm", ".erl", ".fs", ".fsi", ".fsx", ".hcl", ".hrl", ".hs", ".hxx",
    ".jl", ".kts", ".lhs", ".luau", ".m", ".ml", ".mli", ".mm", ".mts",
    ".nim", ".pyi", ".sh", ".tf", ".zsh",
}  # fmt: skip

# Suffixes the pipeline lists but no spec declares (.pl: there is no perl spec).
_CODE_SUFFIXES_ORPHANED = {".pl"}

# Non-tag defensive aliases inside tour._NON_CODE_LANGUAGES (none is a
# registry tag; they guard against unnormalized language strings).
_NON_CODE_ALIASES = {
    "cmake", "css", "csv", "html", "ini", "md", "rst", "svg", "text", "txt",
    "xml", "yml",
}  # fmt: skip

# Real is_code=False registry tags MISSING from tour._NON_CODE_LANGUAGES:
# a schema.graphql named entry-like could still earn entry bonuses today.
_NON_CODE_TAGS_MISSING = {"graphql", "openapi", "proto", "sql", "unknown", "xaml"}

# Entry-point patterns that lived ONLY in the (deleted) dead
# LanguageConfig.entry_point_patterns table — these languages' entry
# flagging is OFF today; merging them into the specs is Phase 1 work.
_PHASE1_ENTRY_PATTERN_BACKLOG = {
    "kotlin": ("Main.kt", "Application.kt"),
    "ruby": ("main.rb", "app.rb", "config.ru"),
    "swift": ("main.swift", "App.swift"),
    "scala": ("Main.scala", "App.scala"),
    "php": ("index.php", "public/index.php"),
}


class TestDriftManifests:
    def test_code_suffix_drift_is_exactly_as_recorded(self) -> None:
        registry_suffixes = REGISTRY.all_code_extensions()
        assert registry_suffixes - _CODE_SUFFIXES == _CODE_SUFFIXES_MISSING
        assert _CODE_SUFFIXES - registry_suffixes == _CODE_SUFFIXES_ORPHANED

    def test_non_code_language_drift_is_exactly_as_recorded(self) -> None:
        tags = {s.tag for s in REGISTRY.all_specs()}
        assert _NON_CODE_LANGUAGES - tags == _NON_CODE_ALIASES
        non_code_tags = {s.tag for s in REGISTRY.all_specs() if not s.is_code}
        assert non_code_tags - _NON_CODE_LANGUAGES == _NON_CODE_TAGS_MISSING

    def test_backlogged_entry_patterns_still_absent_from_specs(self) -> None:
        # When Phase 1 merges these into the specs, delete the backlog entry
        # here and extend the spec's own tests instead.
        for tag, patterns in _PHASE1_ENTRY_PATTERN_BACKLOG.items():
            spec = REGISTRY.get(tag)
            assert spec is not None
            for pattern in patterns:
                assert pattern not in spec.entry_point_patterns, (tag, pattern)
