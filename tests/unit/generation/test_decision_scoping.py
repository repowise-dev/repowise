"""Module pages get their own decisions, ranked (#1587).

Two halves. The behaviour half exercises the helpers directly. The guard half
is an architecture check in the style of ``test_no_test_path_copies``: a page
type must not go back to slicing the repo-wide list, because the slice is not
only rendered, it is prompt context for the page being written.
"""

from __future__ import annotations

import re
from pathlib import Path

from repowise.core.generation.page_generator.helpers import (
    decisions_for_files,
    rank_decisions,
)

LEVELS = Path(__file__).resolve().parents[3] / (
    "packages/core/src/repowise/core/generation/page_generator/levels.py"
)


def _decision(title: str, *, confidence: float | None = 1.0, evidence: str = "e.py") -> dict:
    payload = {"title": title, "source": "git", "evidence_file": evidence}
    if confidence is not None:
        payload["confidence"] = confidence
    return payload


def test_module_decisions_are_scoped_to_the_modules_files():
    a1 = _decision("a1", confidence=0.4, evidence="a/x.py")
    a2 = _decision("a2", confidence=0.9, evidence="a/y.py")
    b1 = _decision("b1", confidence=1.0, evidence="b/z.py")
    by_file = {"a/x.py": [a1, a2], "a/y.py": [a2], "b/z.py": [b1]}

    module_a = decisions_for_files(by_file, ["a/x.py", "a/y.py"])
    assert [d["title"] for d in module_a] == ["a2", "a1"], "most confident first"

    module_b = decisions_for_files(by_file, ["b/z.py"])
    assert [d["title"] for d in module_b] == ["b1"], "another module's decisions do not leak in"

    assert decisions_for_files(by_file, ["unindexed.py"]) == []


def test_a_decision_affecting_several_files_appears_once():
    shared = _decision("shared", evidence="a/x.py")
    by_file = {"a/x.py": [shared], "a/y.py": [shared], "a/z.py": [shared]}

    assert len(decisions_for_files(by_file, ["a/x.py", "a/y.py", "a/z.py"])) == 1


def test_two_records_sharing_a_title_are_not_collapsed():
    # The dedupe key mirrors the decision_records uniqueness constraint
    # (title, source, evidence_file), so a repeated title from a different
    # source or file is a different decision.
    same_title_other_file = [
        _decision("Use X", evidence="a/x.py"),
        _decision("Use X", evidence="b/z.py"),
    ]
    by_file = {"a/x.py": same_title_other_file}

    assert len(decisions_for_files(by_file, ["a/x.py"])) == 2


def test_ranking_is_stable_and_tolerates_a_missing_confidence():
    ranked = rank_decisions(
        [_decision("low", confidence=0.1), _decision("none", confidence=None), _decision("high")]
    )
    assert [d["title"] for d in ranked] == ["high", "low", "none"]

    # Equal confidence keeps discovery order rather than reshuffling.
    tied = rank_decisions([_decision("first"), _decision("second"), _decision("third")])
    assert [d["title"] for d in tied] == ["first", "second", "third"]


def test_no_page_type_slices_the_repo_wide_decision_list_unranked():
    """A raw ``decisions_all`` slice is the bug this closes.

    Repo-wide scope is correct for the overview; taking its first N is not.
    Anything reaching for ``decisions_all`` has to say how it ordered them.
    """
    source = LEVELS.read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in source.splitlines()
        if re.search(r"decisions_all\s*\[", line) and "rank_decisions" not in line
    ]
    assert not offenders, "slice a ranked list, or scope it with decisions_for_files: " + "; ".join(
        offenders
    )
