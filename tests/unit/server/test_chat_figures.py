"""The post-turn numeric check: what counts as a figure and what matches one."""

from __future__ import annotations

import pytest

from repowise.server import chat_figures
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
    return [(f.text, f.verified) for f in check_figures(answer, [("a1", _SOURCE)], question).figures]


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
    figures = check_figures("63 commits", [("a1", {"n": 5}), ("a2", {"n": 63})]).figures

    assert [f.to_json() for f in figures] == [
        {"text": "63", "start": 0, "end": 2, "verified": True, "artifact_id": "a2"}
    ]


def test_offsets_are_utf16_units():
    answer = "\N{ROCKET} 99 bugs"
    (figure,) = check_figures(answer, []).figures

    assert (figure.start, figure.end) == (3, 5)
    assert answer.encode("utf-16-le")[figure.start * 2 : figure.end * 2].decode("utf-16-le") == "99"


def test_an_answer_without_digits_is_not_checked():
    assert check_figures("No numbers.", [("a1", _SOURCE)]).figures == []


def test_a_signed_figure_matches_only_its_sign():
    sources = [("a1", {"delta": 5, "drop": -7})]

    def verdicts(answer):
        return [(f.text, f.verified) for f in check_figures(answer, sources).figures]

    assert verdicts("Health moved by -5.") == [("-5", False)]
    assert verdicts("Health moved by -7, a drop of 7.") == [("-7", True), ("7", True)]
    # A range keeps each end unsigned.
    assert verdicts("Between 5-7 points.") == [("5", True), ("7", True)]


def test_figures_in_cjk_prose_are_checked():
    answer = "该文件有 63 次提交，覆盖率为75%。"

    assert _verdicts(answer) == [("63", True), ("75%", False)]


def test_earlier_turns_are_read_only_for_figures_this_turn_does_not_hold():
    check = check_figures(
        "3 callers and 63 commits",
        [("now", {"callers": 3})],
        earlier=[("before", {"commits": 63, "callers": 3})],
    )

    assert [(f.text, f.artifact_id) for f in check.figures] == [("3", "now"), ("63", "before")]


def test_evidence_past_the_read_cap_leaves_unmatched_figures_not_checked(monkeypatch):
    monkeypatch.setattr(chat_figures, "MAX_NODES_PER_ARTIFACT", 10)
    rows = [{"n": i} for i in range(1000)]

    # Evidence is read in document order, so the cap leaves the tail unread.
    check = check_figures("Found 5 and 999.", [("a1", {"total": 5, "rows": rows})])

    assert [(f.text, f.verified) for f in check.figures] == [("5", True)]
    assert check.not_checked == 1
    assert check.diagnostics() == {
        "figures_checked": 1,
        "figures_unverified": 0,
        "figures_not_checked": 1,
    }


_COUNTS = [
    (
        "a1",
        {
            "total": 505,
            "by_category": {
                "fix": 320, "feature": 115, "refactor": 36, "docs": 15, "test": 11, "chore": 7,
                "other": 1,
            },
            "agent": 254,
            "unknown": 251,
            "files": 12,
            "functions": 40,
            "modules": 3,
            "files_changed": 10,
            "owners": 2,
            "tracked": 4,
        },
    )
]


def _derived(answer: str) -> list[tuple[str, bool, bool]]:
    return [(f.text, f.verified, f.derived) for f in check_figures(answer, _COUNTS).figures]


def _flag(answer: str, figure: str) -> tuple[bool, bool]:
    return next((v, d) for t, v, d in _derived(answer) if t == figure)


_WALK_TABLE = """Total: 505 changes

| Category | Changes | Share |
|---|---|---|
| Fix | 320 | 63.4% |
| Feature | 115 | 22.8% |
| Refactor | 36 | 7.1% |
| Docs | 15 | 3.0% |
| Test | 11 | 2.2% |
| Chore | 7 | 1.4% |
| Other | 1 | 0.2% |
"""


def test_the_walks_share_column_is_derived():
    shares = [(t, v) for t, v, _ in _derived(_WALK_TABLE) if t.endswith("%")]
    assert shares and all(v for _, v in shares), shares
    assert _flag(_WALK_TABLE, "63.4%") == (True, True)
    assert _flag(_WALK_TABLE, "0.2%") == (True, True)
    assert check_figures(_WALK_TABLE, _COUNTS).diagnostics()["figures_derived"] >= 2


def test_a_ratio_written_as_its_two_terms_is_derived():
    assert _flag("Provenance was available for 254 of 505 changes (50.3%).", "50.3%") == (True, True)
    assert _flag("254 out of 505, or 50%.", "50%") == (True, True)


def test_a_sum_written_out_is_derived():
    assert _flag("Fix and feature: 320 + 115 = 435.", "435") == (True, True)
    assert _flag("320 + 115, 435 in all.", "435") == (True, True)


@pytest.mark.parametrize(
    ("answer", "figure"),
    [
        # Two matched numbers that divide to the figure, not written as a ratio.
        ("It changed 12 files and 40 functions; 30% of them are hotspots.", "30%"),
        ("It changed 12 files and 40 functions, 30% of them hotspots.", "30%"),
        # Coincidental small integers.
        ("There are 2 owners and 4 tracked files, so 50% is covered.", "50%"),
        # A total that is not the sum written out.
        ("10 files in 3 modules, 13 total.", "13"),
        # The terms sit in another clause.
        ("Total 505.\nFix: 320, or 63%.", "63%"),
        ("Total: 505.\nFix had 320. That is 63.4% of the work.", "63.4%"),
        # A wrong share, and a share whose terms are not held.
        ("254 of 505 changes (41.0%).", "41.0%"),
        ("77 of 505 changes (15.2%).", "15.2%"),
        # One table row agreeing with a total is not a column.
        ("Total: 4\n\n| a | 2 | 50% |\n| b | 1 | 10% |", "50%"),
    ],
)
def test_a_figure_without_its_working_is_still_flagged(answer, figure):
    assert _flag(answer, figure) == (False, False)
