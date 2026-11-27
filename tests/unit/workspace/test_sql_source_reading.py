"""A comment of a SQL literal is the one the tokenizer reads."""

from __future__ import annotations

from repowise.core.workspace.extractors.data.sql_strings import _cte_names, _without_comments


class TestSqlStrings:
    """The comments of a SQL literal and the names it binds with WITH."""

    def test_a_comment_inside_a_comment_ends_where_the_tokenizer_ends_it(self) -> None:
        literal = (
            "WITH a AS (SELECT 1), /* outer /* inner */ b AS (SELECT 2) */ c AS (SELECT 3) "
            "SELECT * FROM a, c"
        )
        assert _cte_names(literal) == {"a", "c"}

    def test_a_line_comment_and_a_quoted_marker_between_names(self) -> None:
        """A guard: the tokenizer reads a double-quoted name and a quoted string whole."""
        literal = (
            'WITH a AS (SELECT \'x -- y\'), -- note, b AS (\n"c" AS (SELECT 3) SELECT * FROM a, "c"'
        )
        assert _cte_names(literal) == {"a", "c"}

    def test_a_name_quoted_with_backticks_or_brackets_may_hold_two_dashes(self) -> None:
        for quoted in ("`a--b`", "[a--b]"):
            literal = f"WITH {quoted} AS (SELECT 1), c AS (SELECT 2) SELECT * FROM {quoted}, c"
            assert _cte_names(literal) == {"a--b", "c"}

    def test_the_comments_of_a_literal_the_tokenizer_cannot_read_are_still_comments(self) -> None:
        literal = "WITH a AS (SELECT 1), /* note */ b AS (SELECT 2) SELECT 'open FROM a, b"
        assert _cte_names(literal) == {"a", "b"}

    def test_a_comment_is_blanked_where_it_stands(self) -> None:
        literal = "WITH a AS (SELECT 1), -- note\n b AS (SELECT 2) SELECT * FROM a, b"
        blanked = _without_comments(literal)
        assert len(blanked) == len(literal)
        assert blanked.index("\n") == literal.index("\n")
        assert "note" not in blanked
