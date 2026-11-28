"""A comment of a SQL literal is the one the tokenizer reads."""

from __future__ import annotations

from repowise.core.workspace.extractors.data.sql_strings import _cte_scopes, _without_comments


def names_bound(literal: str) -> set[str]:
    return {name for scope in _cte_scopes(literal) for name in scope.names}


class TestSqlStrings:
    """The comments of a SQL literal and the names it binds with WITH."""

    def test_a_comment_inside_a_comment_ends_where_the_tokenizer_ends_it(self) -> None:
        literal = (
            "WITH a AS (SELECT 1), /* outer /* inner */ b AS (SELECT 2) */ c AS (SELECT 3) "
            "SELECT * FROM a, c"
        )
        assert names_bound(literal) == {"a", "c"}

    def test_a_line_comment_and_a_quoted_marker_between_names(self) -> None:
        """A guard: the tokenizer reads a double-quoted name and a quoted string whole."""
        literal = (
            'WITH a AS (SELECT \'x -- y\'), -- note, b AS (\n"c" AS (SELECT 3) SELECT * FROM a, "c"'
        )
        assert names_bound(literal) == {"a", "c"}

    def test_a_name_quoted_with_backticks_or_brackets_may_hold_two_dashes(self) -> None:
        for quoted in ("`a--b`", "[a--b]"):
            literal = f"WITH {quoted} AS (SELECT 1), c AS (SELECT 2) SELECT * FROM {quoted}, c"
            assert names_bound(literal) == {"a--b", "c"}

    def test_the_comments_of_a_literal_the_tokenizer_cannot_read_are_still_comments(self) -> None:
        literal = "WITH a AS (SELECT 1), /* note */ b AS (SELECT 2) SELECT 'open FROM a, b"
        assert names_bound(literal) == {"a", "b"}

    def test_a_comment_inside_a_comment_ends_at_its_own_end_in_a_literal_the_tokenizer_cannot_read(
        self,
    ) -> None:
        literal = (
            "WITH a AS (SELECT 1), /* outer /* inner */ b AS (SELECT 2) */ c AS (SELECT 3) "
            "SELECT 'open FROM a, c"
        )
        assert names_bound(literal) == {"a", "c"}

    def test_a_body_or_a_parenthesis_longer_than_the_scan_limit_is_read_to_its_end(self) -> None:
        values = "1, " * 20_000
        literal = (
            f"SELECT * FROM (WITH a AS (SELECT 1 FROM t WHERE x IN ({values}1)), b AS (SELECT 2) "
            f"SELECT * FROM a, b WHERE y IN ({values}1)) q JOIN b ON 1 = 1"
        )
        assert [(s.names, s.end) for s in _cte_scopes(literal)] == [
            (["a", "b"], literal.index(") q"))
        ]

    def test_a_comment_is_blanked_where_it_stands(self) -> None:
        literal = "WITH a AS (SELECT 1), -- note\n b AS (SELECT 2) SELECT * FROM a, b"
        blanked = _without_comments(literal)
        assert len(blanked) == len(literal)
        assert blanked.index("\n") == literal.index("\n")
        assert "note" not in blanked
