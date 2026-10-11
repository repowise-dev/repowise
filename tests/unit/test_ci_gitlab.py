"""``repowise.core.ci.gitlab``: the GitLab Code Quality issue list."""

from __future__ import annotations

import pytest

from repowise.core.ci import gitlab


def test_issue_has_the_required_fields():
    assert gitlab.issue("path", "major", "msg", "\\docs\\a.md", 7, "abc") == {
        "description": "msg",
        "check_name": "path",
        "fingerprint": "abc",
        "severity": "major",
        "location": {"path": "docs/a.md", "lines": {"begin": 7}},
    }


@pytest.mark.parametrize("line", [None, 0])
def test_an_issue_without_a_line_sits_on_line_one(line):
    assert gitlab.issue("r", "info", "m", "/a.py", line, "f")["location"] == {
        "path": "a.py",
        "lines": {"begin": 1},
    }


def test_an_unknown_severity_is_refused():
    with pytest.raises(ValueError, match="high"):
        gitlab.issue("r", "high", "m", "a.py", 1, "f")


def test_report_names_the_tool_and_makes_repeated_fingerprints_unique_in_order():
    issues = [gitlab.issue("r", "minor", str(i), "a.py", i, fp) for i, fp in enumerate("aaba")]
    out = gitlab.report("tool", issues)
    assert [i["fingerprint"] for i in out] == ["a", "a:2", "b", "a:3"]
    assert {i["check_name"] for i in out} == {"tool/r"}
    assert (issues[1]["fingerprint"], issues[1]["check_name"]) == ("a", "r")  # not mutated


def test_a_suffix_never_collides_with_a_given_fingerprint():
    issues = [gitlab.issue("r", "minor", "m", "a.py", 1, fp) for fp in ("a:2", "a", "a")]
    assert [i["fingerprint"] for i in gitlab.report("t", issues)] == ["a:2", "a", "a:3"]


def test_report_leaves_out_what_the_baseline_accepted():
    issues = [gitlab.issue("r", "minor", "m", "a.py", 1, fp) for fp in ("a", "b", "a")]
    out = gitlab.report("t", issues, accepted=frozenset({"a"}))
    assert [i["fingerprint"] for i in out] == ["b"]
