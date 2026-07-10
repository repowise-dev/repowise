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


class TestContentHash:
    def test_parse_file_stamps_content_hash(self, parser: ASTParser) -> None:
        """Every parse path stamps SHA256(raw bytes) — the cross-run page-reuse key."""
        import hashlib

        source = b"def add(a, b):\n    return a + b\n"
        fi = _make_file_info("pkg/math.py", "python")
        result = parser.parse_file(fi, source)
        assert result.content_hash == hashlib.sha256(source).hexdigest()

    def test_no_grammar_fallback_stamps_content_hash(self, parser: ASTParser) -> None:
        import hashlib

        source = b"some content here"
        fi = _make_file_info("file.xyz", "unknown")
        fi.language = "unknown"
        result = parser.parse_file(fi, source)
        assert result.content_hash == hashlib.sha256(source).hexdigest()

    def test_special_handler_stamps_content_hash(self, parser: ASTParser) -> None:
        """Non-tree-sitter formats (Dockerfile & co) go through parse_special —
        they must be stamped too."""
        import hashlib

        source = b"FROM python:3.12\nRUN pip install repowise\n"
        fi = _make_file_info("Dockerfile", "dockerfile")
        result = parser.parse_file(fi, source)
        assert result.content_hash == hashlib.sha256(source).hexdigest()

    def test_empty_file_stamps_content_hash(self, parser: ASTParser) -> None:
        """sha256(b\"\") is a real, stable hash — empty files reuse like any other."""
        import hashlib

        fi = _make_file_info("pkg/__init__.py", "python")
        result = parser.parse_file(fi, b"")
        assert result.content_hash == hashlib.sha256(b"").hexdigest()


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
