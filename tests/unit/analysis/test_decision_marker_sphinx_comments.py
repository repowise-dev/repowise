"""Sphinx-style ``#:`` comments carry inline decision markers (#6030).

``#:`` is how Sphinx documents an attribute, so rationale written next to one
reads ``#: WHY: ...``. The marker regex allowed only whitespace after ``#``,
and the continuation check counted the prefix's own colon as a new label, so
the whole block was dropped while the same text after ``# `` was found.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.decision_extractor import DecisionExtractor
from repowise.core.analysis.decisions.markers import MARKER_RE, find_markers

_SOURCE = (
    "#: WHY: we keep the old loader because\n"
    "#: the new one breaks X.\n"
    "def load(): ...\n"
    "\n"
    "# WHY: plain marker works because\n"
    "# of this continuation.\n"
    "def other(): ...\n"
)


@pytest.mark.parametrize(
    "line",
    [
        "#: WHY: keep the old loader",
        "    #: DECISION: keep the old loader",
        "#:WHY: keep the old loader",
        "# WHY: keep the old loader",
        "// WHY: keep the old loader",
        "-- WHY: keep the old loader",
        "/* WHY: keep the old loader",
        " * WHY: keep the old loader",
    ],
)
def test_marker_matches_after_every_comment_prefix(line):
    m = MARKER_RE.match(line)
    assert m is not None
    assert m.group("text") == "keep the old loader"


@pytest.mark.parametrize("line", ["#: why: keep the old loader", "#: Why: keep it"])
def test_sphinx_prefix_keeps_keywords_case_sensitive(line):
    assert MARKER_RE.match(line) is None


def test_sphinx_marker_takes_its_sphinx_continuation():
    markers = find_markers("app/loader.py", _SOURCE)

    assert [(m["line"], m["text"]) for m in markers] == [
        (1, "we keep the old loader because the new one breaks X."),
        (5, "plain marker works because of this continuation."),
    ]


@pytest.mark.parametrize("prefix", ["#", "#:"])
def test_labelled_line_still_ends_the_continuation(prefix):
    """A second label after either prefix is a new line of its own, not prose."""
    markers = find_markers(
        "app.py",
        f"{prefix} WHY: keep the old loader\n{prefix} Note: unrelated label\n",
    )

    assert len(markers) == 1
    assert markers[0]["text"] == "keep the old loader"


async def test_sphinx_marker_reaches_the_decision_store(tmp_path):
    """End to end, with no provider: both blocks become decisions."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "loader.py").write_text(_SOURCE, encoding="utf-8")

    decisions = await DecisionExtractor(repo_path=tmp_path).scan_inline_markers()

    assert sorted(d.evidence_line for d in decisions) == [1, 5]
