"""Language specification dataclass — pure data, no behaviour.

``LanguageSpec`` captures everything repowise needs to know about a
language's *identity*: file extensions, classification flags, ecosystem
metadata, builtin symbols, and display properties.

It deliberately excludes parser-specific concerns (tree-sitter node-type
mappings, visibility functions, extractor callables) which belong in
``parser.py``'s ``LanguageConfig``.  This separation keeps the registry
a leaf dependency — it imports nothing from the ingestion pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LanguageSpec:
    """Complete identity specification for a single language."""

    # -- Identity --------------------------------------------------------
    tag: str  # matches LanguageTag literal
    display_name: str  # "Python", "C#", "C/C++"

    # -- File matching ---------------------------------------------------
    extensions: frozenset[str] = field(default_factory=frozenset)  # (".py", ".pyi")
    special_filenames: frozenset[str] = field(default_factory=frozenset)  # ("Dockerfile",)

    # -- Classification --------------------------------------------------
    is_code: bool = True  # False for yaml, json, markdown, etc.
    is_infra: bool = False  # True for dockerfile, makefile, terraform, shell
    is_passthrough: bool = False  # True = no AST parser (config/data/markup)
    is_api_contract: bool = False  # True for proto, graphql, openapi

    # -- Tree-sitter -----------------------------------------------------
    grammar_package: str | None = None  # "tree_sitter_python"
    grammar_loader: str = "language"  # function name in grammar package
    scm_file: str | None = None  # "python.scm" — None = no AST queries
    shares_grammar_with: str | None = None  # C shares cpp grammar

    # -- Heritage --------------------------------------------------------
    heritage_node_types: frozenset[str] = field(default_factory=frozenset)

    # -- Knowledge-graph capabilities --------------------------------------
    # How well import edges can be resolved for this language today:
    #   "full"    — dedicated resolver, validated mechanics
    #   "partial" — dedicated resolver with major known gaps
    #   "none"    — generic stem-lookup fallback only (passthrough/non-code)
    # Consumed by the KG validation harness (density floors, honesty checks);
    # the generation pipeline starts consuming it in the degradation work.
    import_support: str = "none"

    # Filename stems that mark an executable/wiring entry point *for this
    # language only* (cross-language stems like "main"/"index" live in the
    # registry's generic set). Union feeds the tour's entry-stem bonus.
    entry_stems: tuple[str, ...] = ()

    # Stems this language contributes to the traverser's is_entry_point
    # *flag* (strong evidence — python's wsgi/asgi). Distinct from
    # entry_stems: the flag set is deliberately tighter than the tour's
    # weak-bonus stem set.
    entry_flag_stems: tuple[str, ...] = ()

    # Test-shaped filename rules this language contributes to layer
    # inference (unions are consumed in generation/layers.py):
    test_stem_prefixes: tuple[str, ...] = ()  # ("test_",)
    test_stem_suffixes: tuple[str, ...] = ()  # ("_test", "_spec")
    test_infixes: tuple[str, ...] = ()  # (".test.", ".spec.")
    test_fixture_stems: tuple[str, ...] = ()  # ("conftest",)

    # Case-sensitive camel-boundary test suffixes, applied ONLY to this
    # language's own extensions — a convention never leaks to other
    # languages' files. ``FooTest.java`` matches; ``latest.java`` and bare
    # ``Test.java`` never do (conventions match with their own case).
    test_camel_suffixes: tuple[str, ...] = ()  # ("Test", "Tests", "IT")

    # Case-sensitive camel-boundary suffixes marking test *fixture* files
    # ("ParameterizedTypeFixtures.java") — support data living in the test
    # tree. A fixtures file provides data, it doesn't verify behavior, so
    # it never faces the test suite in the tour.
    fixture_camel_suffixes: tuple[str, ...] = ()  # ("Fixture", "Fixtures")

    # Multi-segment test-root directory paths, lowercase, matched as
    # consecutive path segments at any depth ("src/it/java"). Single-token
    # roots (tests/, __tests__/) stay in the generic layer table.
    test_dir_paths: tuple[str, ...] = ()

    # Single-segment test-dir tokens that are UNAMBIGUOUS for this
    # language's files. The generic table treats "spec(s)/" as ambiguous
    # (OpenAPI specs, language specs) and demands a test-shaped filename —
    # but a Ruby file under spec/ is RSpec material whatever its name
    # (support helpers, vendored fixtures like okjson.rb included).
    test_dir_tokens: tuple[str, ...] = ()

    # Case-sensitive directory-segment suffixes that mark a test project
    # directory (.NET sibling test projects: "Foo.Tests/").
    test_dir_suffixes: tuple[str, ...] = ()

    # Stems that anchor the tour's closing test-suite stop (conftest-likes).
    suite_anchor_stems: tuple[str, ...] = ()

    # Declaration-descriptor filenames this language reserves
    # (module-info.java, package-info.java): real source files, but they
    # describe a module/package rather than implement anything — never a
    # layer face or test-suite anchor however shallow they sit.
    descriptor_filenames: tuple[str, ...] = ()

    # Extra (dir_hint → layer_name) hints applied ONLY to this language's
    # files (never other languages'), consulted after the generic layer table at each path
    # depth. Three hint shapes, distinguished by the key:
    #   "internal"  — exact lowercase dir-name token (Go internal/ → Service)
    #   "src/bin"   — multi-segment path, matched as consecutive segments
    #   ".Api"/"-cli" — case-sensitive dir-name *suffix* (leading "." or "-"):
    #                 .NET project dirs (Billing.Api/), cargo crate dirs
    #                 (typst-cli/)
    layer_dir_hints: tuple[tuple[str, str], ...] = ()

    # -- Ecosystem -------------------------------------------------------
    entry_point_patterns: tuple[str, ...] = ()  # ("main.py", "app.py")
    manifest_files: tuple[str, ...] = ()  # ("pyproject.toml",)
    lock_files: tuple[str, ...] = ()  # ("poetry.lock",)
    generated_suffixes: tuple[str, ...] = ()  # ("_pb2.py",)
    shebang_tokens: tuple[str, ...] = ()  # ("python",)
    blocked_dirs: tuple[str, ...] = ()  # ("__pycache__",)
    blocked_extensions: tuple[str, ...] = ()  # (".pyc",)

    # -- Builtins --------------------------------------------------------
    builtin_calls: frozenset[str] = field(default_factory=frozenset)
    builtin_parents: frozenset[str] = field(default_factory=frozenset)

    # -- Display ---------------------------------------------------------
    color_hex: str = "#8b5cf6"  # fallback purple ("other")
