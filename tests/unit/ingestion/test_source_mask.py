"""mask_source on JSX: text between tags is not a string or a regex literal."""

from __future__ import annotations

from repowise.core.ingestion.source_mask import mask_source


def test_an_apostrophe_in_jsx_text_does_not_mask_the_next_line() -> None:
    text = "<p>Don't</p>\nconst a = Card;\n"

    # The apostrophe still opens a string, but only to the end of its line.
    assert mask_source(text, ".tsx", strings=True) == "<p>Don'     \nconst a = Card;\n"


def test_a_closing_tag_is_not_a_regex_literal() -> None:
    text = "  </Card>\n"

    assert mask_source(text, ".tsx", strings=True) == text


def test_template_literals_still_span_lines() -> None:
    text = "const t = `a\nCard`;\n"

    assert mask_source(text, ".ts", strings=True) == "const t = ` \n    `;\n"


def test_a_regex_literal_is_still_masked() -> None:
    assert mask_source("const r = /ab/;\n", ".ts", strings=True) == "const r = /   ;\n"
