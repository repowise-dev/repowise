"""The post-turn numeric check: what counts as a figure and what matches one."""

from __future__ import annotations

import pytest

from repowise.server.chat_figures import check_figures

_SOURCE = {
    "coverage": 0.734,
    "elapsed_ms": 1500,
    "size_bytes": 1_258_291,
    "files_total": 1234,
    "summary": "fixed in 63 commits, 12.5% of them reverted, cache 340 KB",
    "callers": [{"path": "a.py"}, {"path": "b.py"}, {"path": "c.py"}],
}


def _verdicts(answer: str, question: str = "") -> list[tuple[str, bool]]:
    return [(f.text, f.verified) for f in check_figures(answer, [("a1", _SOURCE)], question)]


@pytest.mark.parametrize(
    ("answer", "figure"),
    [
        ("Coverage is 73%.", "73%"),
        ("Coverage is 73.4%.", "73.4%"),
        ("Coverage is 0.73.", "0.73"),
        ("Indexing took 1.5 s.", "1.5 s"),
        ("Indexing took 1500 ms.", "1500 ms"),
        ("Indexing took 1.5 seconds.", "1.5 seconds"),
        ("The index is 1.2 MB.", "1.2 MB"),
        ("The cache is 0.33 MB.", "0.33 MB"),
        ("It holds 1,234 files.", "1,234"),
        ("It holds 1.2k files.", "1.2k"),
        ("It holds about 1,200 files.", "1,200"),
        ("It holds roughly 1,300 files.", "1,300"),
        ("It holds 1,200+ files.", "1,200"),
        ("Fixed in 63 commits.", "63"),
        ("12.5% were reverted.", "12.5%"),
        ("13% were reverted.", "13%"),
        ("It has 3 callers.", "3"),
    ],
)
def test_a_figure_a_tool_returned_is_verified_in_any_rounding_or_unit(answer, figure):
    assert _verdicts(answer) == [(figure, True)]


@pytest.mark.parametrize(
    ("answer", "figure"),
    [
        ("Coverage is 75%.", "75%"),
        ("Coverage is 73.9%.", "73.9%"),
        ("It holds 1,200 files.", "1,200"),
        ("It holds 2.4k files.", "2.4k"),
        ("Indexing took 9 s.", "9 s"),
        ("Fixed in 64 commits.", "64"),
    ],
)
def test_a_figure_no_tool_returned_is_flagged(answer, figure):
    assert _verdicts(answer) == [(figure, False)]


@pytest.mark.parametrize(
    "answer",
    [
        "See `chat.py:120` and chat.py:120-140.",
        "Read lines 10-20 and line 42.",
        "Defined around line **485**, lines **1156–1160**.",
        "Released in v1.2.3 and 0.56.0.",
        "Indexed on 2026-10-14T09:30:00Z, in 2025, on Oct 14 and 14 October.",
        "Merged in #3736 at commit 5a502a32.",
        "1. First step\n2. Second step\n- 3) third",
        "Uses utf-8, sha256, x86_64, gpt-5 and the 3rd pass.",
        "```python\nx = 99\n```",
        "Open https://example.com/page/77 or [docs](https://example.com/88).",
        "Ranked top 10, tier 2, phase 4, step 5.",
        "The ratio was 3:1 at 14:30.",
        "See note [4] and (5).",
        "Under packages/v2/src and file2.py.",
    ],
)
def test_numbers_that_are_not_figures_are_not_checked(answer):
    assert _verdicts(answer) == []


def test_a_number_the_question_holds_is_not_checked():
    assert _verdicts("Here are the 7 riskiest files.", question="Show the 7 riskiest files") == []


def test_a_verified_figure_cites_the_artifact_it_came_from():
    figures = check_figures("63 commits", [("a1", {"n": 5}), ("a2", {"n": 63})])

    assert [f.to_json() for f in figures] == [
        {"text": "63", "start": 0, "end": 2, "verified": True, "artifact_id": "a2"}
    ]


def test_offsets_are_utf16_units():
    answer = "\N{ROCKET} 99 bugs"
    (figure,) = check_figures(answer, [])

    assert (figure.start, figure.end) == (3, 5)
    assert answer.encode("utf-16-le")[figure.start * 2 : figure.end * 2].decode("utf-16-le") == "99"


def test_an_answer_without_digits_is_not_checked():
    assert check_figures("No numbers.", [("a1", _SOURCE)]) == []
