"""Unit tests for reading JS/TS config text in ``js_config_values``."""

from __future__ import annotations

from repowise.core.ingestion.languages.js_config_values import strip_comments


class TestStripComments:
    def test_drops_block_and_line_comments(self) -> None:
        assert strip_comments(b"a /* b */ c // d\ne") == b"a  c \ne"

    def test_comment_markers_inside_strings_stay(self) -> None:
        assert strip_comments(b'x("docs/*"); y /* c */') == b'x("docs/*"); y '
        assert strip_comments(b"x('a//b', `c/*d`)") == b"x('a//b', `c/*d`)"

    def test_trailing_line_comment_after_code_is_dropped(self) -> None:
        assert strip_comments(b'a(), // b("c")\nd') == b"a(), \nd"

    def test_unquoted_url_is_not_a_comment(self) -> None:
        assert strip_comments(b"u: https://example.com") == b"u: https://example.com"

    def test_escaped_quote_does_not_end_the_string(self) -> None:
        assert strip_comments(b'"a\\"/*b" // c') == b'"a\\"/*b" '
