"""The parser package keeps its old import surface and wires every language's import rules."""

from __future__ import annotations

import pytest

import repowise.core.ingestion.parser as parser_pkg
from repowise.core.ingestion.extractors.imports import GENERIC_IMPORTS, ImportExtractor
from repowise.core.ingestion.language_configs import LANGUAGE_CONFIGS

# Names other code and open branches import from ``repowise.core.ingestion.parser``.
_KEPT_NAMES = (
    "ASTParser",
    "LANGUAGE_CONFIGS",
    "LanguageConfig",
    "QUERIES_DIR",
    "_PASSTHROUGH_LANGUAGES",
    "_compile_query",
    "_get_language",
    "_load_compiled_query",
    "_run_query",
    "failed_query_languages",
    "grammar_tag_for",
    "log",
    "missing_grammar_languages",
    "parse_file",
    "_call_receiver_from_node",
    "_dedupe_calls",
    "_fold_receiverless_twins",
    "_receiver_text",
    "_match_identity",
    "_refine_symbol_kind",
    "_refine_visibility",
    "_nesting_parent",
    "_bare_reference_names",
    "_generic_import",
    "_rust_use_imports",
    "_cjs_imports",
    "_pascal_imports",
)


@pytest.mark.parametrize("name", _KEPT_NAMES)
def test_old_names_stay_importable(name: str) -> None:
    assert hasattr(parser_pkg, name)


def test_queries_dir_points_at_the_query_files() -> None:
    assert (parser_pkg.QUERIES_DIR / "python.scm").is_file()


def test_every_config_carries_an_import_extractor() -> None:
    for tag, config in LANGUAGE_CONFIGS.items():
        assert isinstance(config.import_extractor, ImportExtractor), tag
    assert LANGUAGE_CONFIGS["go"].import_extractor is GENERIC_IMPORTS
    typescript = LANGUAGE_CONFIGS["typescript"].import_extractor
    assert LANGUAGE_CONFIGS["svelte"].import_extractor is typescript


def test_language_registry_export_is_the_live_registry() -> None:
    from repowise.core.ingestion.parser import grammars

    parser_pkg._get_language("python")
    assert parser_pkg._LANGUAGE_REGISTRY is grammars._LANGUAGE_REGISTRY
    assert "python" in parser_pkg._LANGUAGE_REGISTRY
