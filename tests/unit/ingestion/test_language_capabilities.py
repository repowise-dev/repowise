"""Language capability registry — parity goldens and derivation pins.

Per-language knowledge (test filename conventions, entry stems,
import-support tiers, layer hints) lives on the ``LanguageSpec`` registry;
``generation/layers.py``, ``generation/tour.py``, the traverser, and
``analysis/kg_curation.py`` consume registry derivations.

Two kinds of test live here:

1. **Parity goldens** — the derived unions are pinned exactly. Any spec
   edit that changes a union must update the golden *consciously*: these
   sets steer test detection, entry scoring, and layer inference globally.

2. **Derivation pins** — constants that used to be drifting hard-coded
   literals (``_CODE_SUFFIXES``, ``_NON_CODE_LANGUAGES``) are now registry
   derivations; the pins assert the derivation relationship and its key
   membership so a regression back to drift cannot land silently.
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
        assert frozenset(
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
        ) == _ENTRY_FILENAME_STEMS

    def test_test_stem_prefixes_match_historical_set(self) -> None:
        assert set(layers._TEST_FILE_STEM_PREFIXES) == {"test_"}

    def test_test_stem_suffixes_match_historical_set(self) -> None:
        # "_unittest" (C/C++ GoogleTest convention) was a conscious
        # addition to the historical {"_test", "_spec"} union.
        assert set(layers._TEST_FILE_STEM_SUFFIXES) == {"_test", "_spec", "_unittest"}

    def test_test_infixes_match_historical_set(self) -> None:
        assert set(layers._TEST_FILE_INFIXES) == {".test.", ".spec."}

    def test_test_fixture_stems_match_historical_set(self) -> None:
        assert frozenset(
            {"conftest", "spec_helper", "test_helper"}
        ) == layers._TEST_FIXTURE_STEMS

    def test_suite_anchor_stems(self) -> None:
        # ruby (rspec/minitest helpers) and elixir (ExUnit's
        # test_helper.exs) join python's conftest as closing-stop anchors.
        assert REGISTRY.suite_anchor_stems() == frozenset(
            {"conftest", "spec_helper", "test_helper"}
        )

    def test_descriptor_filenames(self) -> None:
        # JPMS/javadoc descriptors: source files that declare, not implement.
        assert REGISTRY.descriptor_filenames() == frozenset(
            {"module-info.java", "package-info.java"}
        )

    def test_layer_dir_hints_by_language(self) -> None:
        # Per-language hints (consulted after the generic table, only for
        # the declaring language's files). csharp's project-suffix hints
        # await verification against a live .NET repo.
        assert REGISTRY.layer_dir_hints_by_language() == {
            "go": (("internal", "Service"), ("pkg", "Service")),
            "rust": (("-cli", "CLI"), ("src/bin", "CLI")),
            "ruby": (("jobs", "Service"),),
            "csharp": (
                (".Api", "API"),
                (".Domain", "Service"),
                (".Infrastructure", "Data"),
            ),
            # Root-anchored ("/"): only a TOP-LEVEL include/ is a C/C++
            # library's public API surface (libuv, fmt — validated live).
            "c": (("/include", "API"),),
            "cpp": (("/include", "API"),),
        }

    def test_camel_suffix_extension_map(self) -> None:
        # Case-sensitive camel-boundary test suffixes per language.
        camel = REGISTRY.camel_test_res_by_extension()
        assert set(camel) == {
            ".java", ".kt", ".kts", ".scala", ".cs", ".swift", ".php",
            ".hs", ".lhs",
        }
        assert camel[".java"].pattern == r"(?<=[a-z0-9])(?:Tests|Test|IT)$"
        assert camel[".scala"].pattern == r"(?<=[a-z0-9])(?:Suite|Spec|Test)$"

    def test_test_dir_paths_union(self) -> None:
        assert REGISTRY.test_dir_paths() == (
            # "src/*Test" = Gradle source-set wildcard (src/commonTest,
            # src/jvmTest, … — okio, validated live).
            "src/*Test",
            "src/integrationtest/java",
            "src/it/java",
            "src/it/scala",
            "src/test/java",
            "src/test/kotlin",
            "src/test/scala",
        )

    def test_test_dir_suffixes_union(self) -> None:
        # .Specs = BDD-style sibling test projects (Polly, validated live).
        assert REGISTRY.test_dir_suffixes() == (".Specs", ".Tests")


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
    # php + swift promoted after live validation (Slim, Alamofire).
    "php",
    "python",
    "ruby",
    "rust",
    "swift",
    "typescript",
}
_PARTIAL = {
    "luau",
    "scala",
    # Lightweight regex-tier resolvers (module-name index + import regexes).
    "elixir",
    "dart",
    "clojure",
    "haskell",
    "erlang",
    "fsharp",
}


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
# Derivation pins — constants that used to be drifting frozen literals are
# now registry derivations; pin the relationship, not a literal.
# ---------------------------------------------------------------------------

# Non-tag defensive aliases inside tour._NON_CODE_LANGUAGES (none is a
# registry tag; they guard against unnormalized language strings).
_NON_CODE_ALIASES = {
    "cmake", "css", "csv", "html", "ini", "md", "rst", "svg", "text", "txt",
    "xml", "yml",
}  # fmt: skip

# Entry-point conventions per language, including those recovered from the
# deleted dead LanguageConfig table. "public/index.php" was intentionally
# dropped: patterns match bare filenames, and "index.php" subsumes it.
_ENTRY_PATTERNS_BY_LANGUAGE = {
    "kotlin": ("Main.kt", "Application.kt"),
    "ruby": ("main.rb", "app.rb", "config.ru"),
    "swift": ("main.swift", "App.swift"),
    "scala": ("Main.scala", "App.scala"),
    "php": ("index.php", "artisan"),
    "elixir": ("application.ex",),
    "clojure": ("core.clj", "main.clj"),
    "dart": ("main.dart",),
    "haskell": ("Main.hs",),
    "ocaml": ("main.ml",),
    "erlang": ("*_app.erl",),
    "fsharp": ("Program.fs",),
    "crystal": ("main.cr",),
    "nim": ("main.nim",),
    "dlang": ("app.d",),
    "elm": ("Main.elm",),
    "zig": ("main.zig",),
    "objectivec": ("main.m",),
}


class TestDriftManifests:
    def test_code_suffixes_are_the_non_infra_code_derivation(self) -> None:
        assert REGISTRY.non_infra_code_extensions() == _CODE_SUFFIXES
        # The 32 once-missing extensions are protected now …
        assert {".dart", ".hs", ".clj", ".erl", ".nim", ".m", ".luau"} <= _CODE_SUFFIXES
        # … infra languages stay promotable, and the perl orphan is gone.
        assert {".sh", ".bash", ".zsh", ".tf", ".hcl", ".pl"} & _CODE_SUFFIXES == set()

    def test_non_code_languages_are_config_plus_infra_plus_aliases(self) -> None:
        assert (
            REGISTRY.config_languages()
            | REGISTRY.infra_languages()
            | frozenset(_NON_CODE_ALIASES)
        ) == _NON_CODE_LANGUAGES
        # The once-missing is_code=False tags and infra tags are covered …
        assert {
            "graphql", "openapi", "proto", "sql", "unknown", "xaml",
            "shell", "terraform", "dockerfile", "makefile",
        } <= _NON_CODE_LANGUAGES  # fmt: skip
        # … and real (Tier-3 included) code languages never are.
        assert {"python", "elixir", "dart", "haskell", "go"} & _NON_CODE_LANGUAGES == set()

    def test_merged_entry_patterns_present_on_specs(self) -> None:
        # Every language with a citable convention declares it. Deliberate
        # skips: julia (src/<Pkg>.jl is package-named), R (no convention),
        # PHP bin/console (the bare filename "console" is too generic).
        for tag, patterns in _ENTRY_PATTERNS_BY_LANGUAGE.items():
            spec = REGISTRY.get(tag)
            assert spec is not None, tag
            assert spec.entry_point_patterns == patterns, tag

    def test_entry_flag_stems_match_historical_traverser_set(self) -> None:
        # The traverser's is_entry_point stem set, now registry-derived —
        # parity with the historical hard-coded frozenset (run.py/server.py
        # extras were redundant with the run/server stems).
        assert REGISTRY.entry_flag_stems() == frozenset(
            {"main", "index", "app", "run", "server", "start", "wsgi", "asgi"}
        )
