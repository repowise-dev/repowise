"""The shared FTS5 expression builder.

Tested here rather than only through ``persistence.search``, which is where it
used to live: it now has two callers with different corpora, and a module whose
only test reaches it through one caller loses its own identity the moment that
caller is refactored.

The escaping is the part these tests exist for. Every character that could
change the shape of an FTS5 expression has to be gone before a term is built,
because the second caller is a sidecar store that cannot import the first.
"""

from __future__ import annotations

import pytest

from repowise.core.fts_query import (
    PREFIX_MIN_CHARS,
    STOP_WORDS,
    build_fts5_query,
    match_term,
    meaningful_terms,
)


class TestMeaningfulTerms:
    def test_stop_words_and_single_characters_are_dropped(self) -> None:
        assert meaningful_terms("How does the a b walker work") == ["walker", "work"]

    @pytest.mark.parametrize(
        "hostile",
        ['walker" OR "', "walker*", "(walker)", "walker: NEAR(a b)", "walker^2", "-walker"],
    )
    def test_fts5_syntax_never_survives_tokenizing(self, hostile: str) -> None:
        """The reduction to [a-zA-Z0-9_] is the escaping; nothing else has to be."""
        for term in meaningful_terms(hostile):
            assert term.isascii()
            assert all(c.isalnum() or c == "_" for c in term)

    def test_stop_words_are_the_shared_set(self) -> None:
        assert "the" in STOP_WORDS
        assert "walker" not in STOP_WORDS


class TestMatchTerm:
    def test_a_long_term_gets_a_prefix_wildcard(self) -> None:
        assert match_term("x" * PREFIX_MIN_CHARS) == f'"{"x" * PREFIX_MIN_CHARS}"*'

    def test_a_short_term_is_matched_exactly(self) -> None:
        short = "x" * (PREFIX_MIN_CHARS - 1)
        assert match_term(short) == f'"{short}"'


class TestBuildQuery:
    def test_terms_are_or_joined(self) -> None:
        built = build_fts5_query("walker nested checkouts")
        assert built.count(" OR ") == 2

    def test_a_question_of_only_stop_words_falls_back_to_a_phrase(self) -> None:
        assert build_fts5_query("how does it") == '"how does it"'

    def test_the_phrase_fallback_doubles_its_quotes(self) -> None:
        """The one path where raw text reaches the expression."""
        assert build_fts5_query('how "it" is') == '"how ""it"" is"'

    def test_a_document_frequency_ceiling_drops_common_terms(self) -> None:
        """Four terms, because the floor keeps three whatever the ceiling says."""
        counts = {"": 1000, "walker": 10, "file": 900, "checkouts": 20, "nested": 30}
        built = build_fts5_query("walker file checkouts nested", counts.__getitem__)
        assert '"file"' not in built
        assert '"walker"*' in built

    def test_the_ceiling_never_empties_a_query(self) -> None:
        counts = {"": 1000, "file": 900, "page": 950, "index": 980, "does": 990}
        built = build_fts5_query("file page index does", counts.__getitem__)
        assert built
