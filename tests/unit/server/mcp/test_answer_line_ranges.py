"""Synthesis sees each served body's line range, and citing it is not a value claim.

Without the range no provider can cite lines, and agents re-read whole files to
get them. Once answers cite "(line 27)", the number-grounding gate must read
that as a citation, or every value answer that cites its line grades low.
"""

from __future__ import annotations

import pytest

from repowise.server.mcp_server._answer_context import build_context_block
from repowise.server.mcp_server.tool_answer.confidence import (
    _asserted_numbers,
    _ungrounded_numbers,
)


def _hit(start_line: int, source: str) -> dict:
    return {
        "target_path": "pkg/client.py",
        "title": "client",
        "summary": "",
        "symbols": [
            {
                "name": "redirect",
                "kind": "function",
                "signature": "def redirect(url)",
                "start_line": start_line,
                "end_line": start_line + source.count("\n"),
                "source_excerpt": source,
            }
        ],
    }


def test_served_body_carries_its_line_range() -> None:
    src = "def redirect(url):\n    limit = 20\n    return url"
    prompt = build_context_block([_hit(40, src)])
    assert "source (lines 40-42):" in prompt


def test_body_without_a_start_line_has_no_range() -> None:
    hit = _hit(40, "def redirect(url):\n    return url")
    hit["symbols"][0]["start_line"] = 0
    assert "          source:" in build_context_block([hit])


@pytest.mark.parametrize(
    "text",
    [
        "It is defined in `client.py` (line 27).",
        "See lines 26-27.",
        "It reads the flag (`cobra.go`, lines 46 and 55).",
        "Lines 10 to 12 build it.",
        "The body (L40-L52) reads it.",
        "In client.py:27-29 it is set.",
    ],
)
def test_line_citations_are_not_asserted_values(text: str) -> None:
    assert _asserted_numbers(text) == set()


def test_values_next_to_citations_are_still_asserted() -> None:
    text = "The limit is 20 (line 41); the line length stays 120."
    assert _asserted_numbers(text) == {"20", "120"}


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("It reads max lines 500.", {"500"}),
        ("The cursor is set to line 30.", {"30"}),
        ("It waits between lines 10 and 30 seconds.", {"10", "30"}),
    ],
)
def test_a_number_after_line_outside_a_citation_is_asserted(text: str, values: set) -> None:
    assert _asserted_numbers(text) == values


def test_a_cited_value_answer_is_grounded_by_the_body() -> None:
    src = "def redirect(url):\n    limit = 20\n    return url"
    answer = "`limit` is 20, set in `client.py` (line 41)."
    # 41 is only in the prompt's range header, never in the grounding corpus.
    assert _ungrounded_numbers(answer, [_hit(40, src)]) == []
    assert _ungrounded_numbers("`limit` is 30 (line 41).", [_hit(40, src)]) == ["30"]
