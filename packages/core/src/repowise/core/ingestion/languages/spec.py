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
