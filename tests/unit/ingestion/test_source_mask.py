"""mask_source on JSX: text between tags is not a string or a regex literal."""

from __future__ import annotations

import pytest

from repowise.core.ingestion.source_mask import mask_source


def test_an_apostrophe_in_jsx_text_does_not_mask_the_next_line() -> None:
    text = "<p>Don't</p>\nconst a = Card;\n"

    # The apostrophe still opens a string, but only to the end of its line.
    assert mask_source(text, ".tsx", strings=True) == "<p>Don'     \nconst a = Card;\n"


def test_a_closing_tag_is_not_a_regex_literal() -> None:
    text = "  </Card>\n"

    assert mask_source(text, ".tsx", strings=True) == text


def test_a_self_closing_tag_after_a_braced_attribute_is_not_a_regex_literal() -> None:
    text = (
        "const a = <Item id={id} />; run(a);\n"
        "const b = <Row {...p} />;\n"
        "function f() {\n  return <Cell\n    on={() => g(1)}\n  />;\n}\n"
    )

    assert mask_source(text, ".tsx", strings=True) == text


@pytest.mark.parametrize(
    "text, expected",
    [
        ("const r = {a: 1}\n/>/.test(s)\n", "const r = {a: 1}\n/  .test(s)\n"),
        ("if (a) {}\n/>'/.test(s); run('x');\n", "if (a) {}\n/   .test(s); run(' ');\n"),
        ("if (xreturn <b) {}\n/>/.test(s)\n", "if (xreturn <b) {}\n/  .test(s)\n"),
    ],
    ids=["regex-after-object", "regex-after-block", "comparison-after-name-ending-return"],
)
def test_a_slash_after_a_brace_outside_a_tag_still_opens_a_regex(text, expected) -> None:
    assert mask_source(text, ".ts", strings=True) == expected


@pytest.mark.parametrize(
    "head",
    ["if (this.return < max) {", "if (this .\n    return < max) {"],
    ids=["member-return", "member-return-across-a-line-break"],
)
def test_a_member_named_return_opens_no_tag(head) -> None:
    text = head + "\n  return items.map(x => <Row b={x} />);\n}\n"

    assert mask_source(text, ".tsx", strings=True) == text


def test_template_literals_still_span_lines() -> None:
    text = "const t = `a\nCard`;\n"

    assert mask_source(text, ".ts", strings=True) == "const t = ` \n    `;\n"


def test_a_regex_literal_is_still_masked() -> None:
    assert mask_source("const r = /ab/;\n", ".ts", strings=True) == "const r = /   ;\n"
