"""A change's category from the project's own signals before its title."""

from __future__ import annotations

import pytest

from repowise.core.changes.category import (
    SIGNAL_FRAGMENT,
    SIGNAL_LABEL,
    SIGNAL_TITLE,
    classify_change,
    fragment_category,
    label_category,
)


@pytest.mark.parametrize(
    ("path", "want"),
    [
        ("CHANGES/13743.bugfix.rst", "fix"),
        ("changelog/123.feature.md", "feature"),
        ("news/7402.bugfix", "fix"),
        ("src/twisted/newsfragments/9.doc", "docs"),
        ("changelog.d/12.bugfix.1.rst", "fix"),
        ("CHANGES/+orphan.misc.rst", "other"),
        ("changelog/88.improvement.rst", "other"),
        # Not fragments: a type nobody declares, a Gherkin file, the wrong place.
        ("CHANGES/README.rst", None),
        ("news/2021-01-01.md", None),
        ("features/login.feature", None),
        ("changes/login.feature", None),
        ("news/story.doc", None),
        ("news/0f3a9c1e.trivial.rst", "other"),
        ("src/123.bugfix.rst", None),
        ("13743.bugfix.rst", None),
        (".changeset/brave-dogs-run.md", None),
    ],
)
def test_fragment_category(path, want):
    assert fragment_category(path) == want


@pytest.mark.parametrize(
    ("label", "want"),
    [
        ("bug", "fix"),
        ("type: bug", "fix"),
        ("kind/bug", "fix"),
        ("\U0001f41b Bug", "fix"),
        ("type: enhancement", "feature"),
        ("documentation", "docs"),
        ("needs-review", None),
        ("area/docs", None),
        ("area: test", None),
        ("bot:chronographer:provided", None),
    ],
)
def test_label_category(label, want):
    assert label_category(label) == want


def test_fragment_beats_the_title():
    basis = classify_change(
        "Close the proxy connection when the tunnel fails",
        [("aiohttp/connector.py", 10, 2), ("CHANGES/13841.bugfix.rst", 1, 0)],
    )
    assert basis.category == "fix"
    assert (basis.signal, basis.value) == (SIGNAL_FRAGMENT, "CHANGES/13841.bugfix.rst")


def test_a_non_fix_fragment_overrules_a_fix_title():
    basis = classify_change("Fix the CI cache key", [("CHANGES/9.contrib.rst", 1, 0)])
    assert (basis.category, basis.signal) == ("other", SIGNAL_FRAGMENT)


def test_a_fix_fragment_wins_over_other_kinds_in_one_change():
    files = [("changelog/5.feature.rst", 1, 0), ("changelog/5.bugfix.rst", 1, 0)]
    assert classify_change("Rework the runner", files).category == "fix"


def test_a_release_deleting_fragments_is_not_their_kind():
    basis = classify_change(
        "Release 3.9.0", [("CHANGES/1.bugfix.rst", 0, 3), ("CHANGES.rst", 40, 0)]
    )
    assert basis.signal == SIGNAL_TITLE


def test_label_then_title():
    basis = classify_change("Handle empty headers", [("a.py", 1, 1)], ["type: bug", "needs-review"])
    assert (basis.category, basis.signal, basis.value) == ("fix", SIGNAL_LABEL, "type: bug")
    # A fragment outranks a label the project put on the pull request.
    basis = classify_change("x", [("news/1.feature.rst", 1, 0)], ["bug"])
    assert (basis.category, basis.signal) == ("feature", SIGNAL_FRAGMENT)


def test_no_project_signal_keeps_the_title_rule():
    basis = classify_change("fix(parser): reject a third coding", [("a.py", 3, 1)], None)
    assert (basis.category, basis.signal, basis.value) == ("fix", SIGNAL_TITLE, None)
    assert classify_change("Speed up the parser", [("a.py", 3, 1)]).category == "other"
