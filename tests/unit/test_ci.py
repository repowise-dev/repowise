"""Shared CI plumbing: workflow commands, markdown pieces, the target branch."""

from __future__ import annotations

import subprocess

import pytest

from repowise.core.ci import github
from repowise.core.ci.base import BaseNotFoundError, default_revspec
from repowise.core.ci.markdown import cell, details, more_line, plural


def test_annotation_escapes_properties_and_message() -> None:
    line = github.annotation(
        "warning", "50% done\nnext", file="a,b:c.py", line=3, end_line=4, title="T: x"
    )
    assert line == "::warning file=a%2Cb%3Ac.py,line=3,endLine=4,title=T%3A x::50%25 done%0Anext"


def test_bare_commands_share_the_builder() -> None:
    assert github.error("boom: 5%") == "::error::boom: 5%25"
    assert github.notice("hi") == "::notice::hi"


def test_cap_annotations_counts_what_it_cut() -> None:
    lines = [f"::warning::{n}" for n in range(13)]
    capped = github.cap_annotations(lines, noun="ranges")
    assert capped[:10] == lines[:10]
    assert capped[10] == "::notice::3 more ranges, listed in the job summary"
    assert github.cap_annotations(lines[:4]) == lines[:4]


def test_markdown_pieces() -> None:
    assert cell("a|b`c<d") == "a\\|b\\`c&lt;d"
    assert plural(1, "file") == "1 file"
    assert plural(2, "file") == "2 files"
    assert more_line(0, "files") == ""
    assert more_line(3, "files") == "and 3 more files."
    out = details("S", [str(n) for n in range(12)], limit=10)
    assert out[:3] == ["<details>", "<summary>S</summary>", ""]
    assert out[-3:] == ["- and 2 more", "", "</details>"]
    assert details("S", []) == []


def _git(cwd, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def detached_repo(tmp_path):
    """A CI-shaped checkout: detached, no local trunk, no origin/HEAD."""
    _git(tmp_path, "init", "-q", "-b", "work")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "f").write_text("x", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    _git(tmp_path, "checkout", "-q", "--detach")
    _git(tmp_path, "branch", "-D", "work")
    return tmp_path


def test_default_revspec_prefers_ci_variables(detached_repo) -> None:
    env = {"CHANGE_TARGET": "release", "GITHUB_BASE_REF": ""}
    assert default_revspec(str(detached_repo), env) == "origin/release...HEAD"


def test_default_revspec_falls_back_to_remote_trunk(detached_repo) -> None:
    _git(detached_repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert default_revspec(str(detached_repo), {}) == "origin/main...HEAD"


def test_default_revspec_raises_when_nothing_names_a_base(detached_repo) -> None:
    with pytest.raises(BaseNotFoundError):
        default_revspec(str(detached_repo), {})


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"CI": "true"}, True),
        ({"GITHUB_BASE_REF": "main"}, True),
        ({"CI": "false"}, False),
        ({"CI": "", "GITHUB_BASE_REF": " "}, False),
        ({}, False),
    ],
)
def test_in_ci_reads_the_ci_variables(env, expected) -> None:
    from repowise.core.ci.base import in_ci

    assert in_ci(env) is expected
