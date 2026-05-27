"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

from repowise.core.ingestion.parser import LANGUAGE_CONFIGS, ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info


class TestUnsupportedLanguage:
    def test_returns_empty_parsed_file(self, parser: ASTParser) -> None:
        """Unsupported languages return an empty ParsedFile with no errors
        (silent passthrough by design — see parser.py line 354)."""
        fi = _make_file_info("file.xyz", "unknown")
        fi.language = "unknown"
        result = parser.parse_file(fi, b"some content here")
        assert result.symbols == []
        assert result.imports == []
        assert result.parse_errors == []


class TestLanguageConfigs:
    def test_all_supported_languages_have_config(self) -> None:
        expected = {
            "python",
            "typescript",
            "javascript",
            "go",
            "rust",
            "java",
            "cpp",
            "c",
            "kotlin",
            "ruby",
            "csharp",
            "swift",
            "scala",
            "php",
            "luau",
        }
        for lang in expected:
            assert lang in LANGUAGE_CONFIGS, f"Missing config for {lang}"

    def test_each_config_has_symbol_node_types(self) -> None:
        for lang, config in LANGUAGE_CONFIGS.items():
            assert len(config.symbol_node_types) > 0, f"{lang} has no symbol_node_types"

    def test_each_config_has_visibility_fn(self) -> None:
        for lang, config in LANGUAGE_CONFIGS.items():
            # Must be callable
            result = config.visibility_fn("MyClass", [])
            assert result in ("public", "private", "protected", "internal"), (
                f"{lang} visibility_fn returned unexpected: {result}"
            )
