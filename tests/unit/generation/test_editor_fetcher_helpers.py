"""Unit tests for the pure helpers in editor_files.fetcher.

These guard the CLAUDE.md rendering quality: prose-only sentence
extraction (no jammed list bullets) and word-boundary truncation
(no mid-word chops in generated tables).
"""

from __future__ import annotations

from repowise.core.generation.editor_files.fetcher import (
    _extract_sentences,
    _truncate_at_word,
)


class TestExtractSentences:
    def test_plain_prose(self) -> None:
        text = "First sentence here. Second sentence here. Third one follows."
        assert _extract_sentences(text, max_sentences=2) == (
            "First sentence here. Second sentence here."
        )

    def test_strips_headers_and_fences(self) -> None:
        text = "## Overview\n\nThe module does X.\n\n```py\ncode\n```\nIt also does Y."
        out = _extract_sentences(text, max_sentences=4)
        assert "## Overview" not in out
        assert "code" not in out
        assert "The module does X." in out

    def test_list_items_do_not_jam_onto_prose(self) -> None:
        # Regression: bullets after a sentence used to be glued onto it
        # ("...web UI. - **Languages**") because they carry no sentence
        # punctuation of their own.
        text = (
            "The engine outputs a wiki rendered in a web UI.\n\n"
            "- **Languages**\n"
            "  - Python\n"
            "1. **Inputs**\n"
            "   - A target repository workspace.\n"
            "  1) extending the language registry/specs, and\n"
            "  2) implementing a resolver.\n"
            "| col | col |\n"
            "> quoted\n"
            "Documentation is served through an API."
        )
        out = _extract_sentences(text, max_sentences=4)
        assert "- **Languages**" not in out
        assert "1. **Inputs**" not in out
        assert "1) extending" not in out
        assert "| col" not in out
        assert "quoted" not in out
        assert "rendered in a web UI." in out
        assert "served through an API." in out

    def test_colon_lead_ins_are_dropped_with_their_lists(self) -> None:
        # Regression: "Repowise consumes:" survived after its list items were
        # stripped, leaving dangling fragments in the rendered CLAUDE.md.
        text = (
            "Repowise is a documentation engine that produces a wiki.\n"
            "Repowise consumes:\n"
            "- source files\n"
            "- git metadata\n"
            "Think of it as a pipeline with four stages:\n"
            "1. ingest\n"
            "The output is served over MCP."
        )
        out = _extract_sentences(text, max_sentences=4)
        assert "consumes:" not in out
        assert "four stages:" not in out
        assert "produces a wiki." in out
        assert "served over MCP." in out

    def test_empty_input(self) -> None:
        assert _extract_sentences("", max_sentences=3) == ""


class TestTruncateAtWord:
    def test_short_text_unchanged(self) -> None:
        assert _truncate_at_word("short text", 80) == "short text"

    def test_never_cuts_mid_word(self) -> None:
        text = (
            "The ingestion/languages module is the language ingestion "
            "subsystem's classification and resolution layer"
        )
        out = _truncate_at_word(text, 80)
        assert len(out) <= 81  # limit + ellipsis
        assert out.endswith("…")
        # Every emitted word must be a complete word of the input.
        for word in out.rstrip("…").split():
            assert word in text

    def test_exact_limit_unchanged(self) -> None:
        text = "x" * 80
        assert _truncate_at_word(text, 80) == text
