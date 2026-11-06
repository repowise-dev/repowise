"""Unit tests for the pure helpers in editor_files.fetcher.

These guard the CLAUDE.md rendering quality: prose-only sentence
extraction (no jammed list bullets) and word-boundary truncation
(no mid-word chops in generated tables).
"""

from __future__ import annotations

from repowise.core.generation.editor_files.fetcher import (
    _extract_sentences,
    _signed_by,
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

    def test_drops_thematic_break(self) -> None:
        text = "---\n\nThe accounts module manages authentication and sessions."
        got = _extract_sentences(text, max_sentences=1)
        assert got == "The accounts module manages authentication and sessions."

    def test_drops_meta_badge_rows(self) -> None:
        text = (
            "**Language:** python | **Files:** 9 | **Public symbols:** 5 / 5\n\n"
            "The accounts module manages authentication and sessions."
        )
        got = _extract_sentences(text, max_sentences=1)
        assert got == "The accounts module manages authentication and sessions."

    def test_joins_new_lines_into_spaces(self) -> None:
        text = (
            "The accounts module manages\n"
            "authentication and active user\n"
            "sessions across requests."
        )
        got = _extract_sentences(text, max_sentences=1)
        assert "\n" not in got
        assert got == (
            "The accounts module manages authentication and active "
            "user sessions across requests."
        )

    def test_keeps_a_single_bold_label_sentence(self) -> None:
        text = "**Note:** Handles invoicing and payment retries."
        assert _extract_sentences(text, max_sentences=1) == text

    def test_drops_all_emphasis_footer(self) -> None:
        text = (
            "The accounts module manages authentication and sessions.\n\n"
            "*Built from the code's structure. It states what is there, not why it is that\n"
            "way. The explanatory prose is a separate, model-written layer.*\n"
        )
        got = _extract_sentences(text, max_sentences=4)
        assert got == "The accounts module manages authentication and sessions."

    def test_drops_thematic_break_and_all_emphasis_footer(self) -> None:
        text = (
            "---\n\n"
            "*Built from the code's structure. It states what is there, not why it is that\n"
            "way. The explanatory prose is a separate, model-written layer.*\n"
        )
        assert _extract_sentences(text, max_sentences=2) == ""

    def test_keeps_prose_that_starts_and_ends_with_bold(self) -> None:
        text = "**Billing** is used by **reports**"
        assert _extract_sentences(text, max_sentences=1) == text

    def test_skip_preamble_starts_at_first_section(self) -> None:
        text = (
            "# billing\n\n`billing`\n\n"
            "**Language:** python | **Files:** 6\n\n"
            "Covers the 6 source files in billing.\n\n"
            "## Overview\n\n"
            "billing covers 6 python files, exposing 5 public symbols.\n"
        )
        got = _extract_sentences(text, max_sentences=1, skip_preamble=True)
        assert got == "billing covers 6 python files, exposing 5 public symbols."

    def test_skip_preamble_without_a_section_returns_empty(self) -> None:
        text = "# billing\n\n**Language:** python | **Files:** 6\n\nCovers the 6 files."
        assert _extract_sentences(text, max_sentences=1, skip_preamble=True) == ""

    def test_preamble_prose_is_kept_by_default(self) -> None:
        text = "# Billing\n\nHandles invoicing and payment retries.\n\n## Details\n\nMore."
        assert _extract_sentences(text, max_sentences=1) == (
            "Handles invoicing and payment retries."
        )

    def test_skip_preamble_passes_over_a_list_only_section(self) -> None:
        text = (
            "# billing\n\nCovers the 6 files.\n\n"
            "## Parts of this subsystem\n\n"
            "- `billing/a.py` handles invoices\n"
            "- `billing/b.py` handles refunds\n\n"
            "## Overview\n\n"
            "billing covers 6 python files, exposing 5 public symbols.\n"
        )
        got = _extract_sentences(text, max_sentences=1, skip_preamble=True)
        assert got == "billing covers 6 python files, exposing 5 public symbols."


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


class TestSignedBy:
    """A line a person did not sign says so, and costs nothing when they did.

    These files are read into every session, so the mark has to be absent in
    the ordinary case rather than merely short.
    """

    @staticmethod
    def _sig(kind: str, accepter: str = ""):
        from repowise.core.persistence.crud.authority import AcceptanceSignature

        return AcceptanceSignature(kind=kind, accepter=accepter, artifact="", session="")

    def test_a_person_costs_no_tokens(self) -> None:
        assert _signed_by(self._sig("person", "Raghav")) == ""

    def test_a_candidate_costs_no_tokens(self) -> None:
        # Belt and braces: the fetcher only selects accepted records.
        assert _signed_by(None) == ""

    def test_an_agent_is_named_as_one(self) -> None:
        """The block reads as standing rules, so an agent must not find its
        own acceptance there presented as the team's."""
        mark = _signed_by(self._sig("agent", "claude_code"))

        assert "claude_code" in mark
        assert "not a person" in mark

    def test_an_agent_with_no_name_still_says_it_was_one(self) -> None:
        assert "an agent" in _signed_by(self._sig("agent"))

    def test_an_import_is_marked_but_not_called_an_agent(self) -> None:
        mark = _signed_by(self._sig("import", "adr/0001.md"))

        assert mark and "agent" not in mark

    def test_an_unrecorded_kind_is_not_silently_a_person(self) -> None:
        # A row written before provenance existed. Rendering nothing would
        # claim a human signed it.
        assert _signed_by(self._sig("")) == " [signer not recorded]"
