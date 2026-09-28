"""Diff -> changed-line parsing for ``repowise impacted-tests``.

The pure parser is exercised on hand-written unified diffs (no git needed);
:func:`changed_lines` is exercised end-to-end against a real temp git repo so
the range / commit / staged revspec handling is covered too.
"""

from __future__ import annotations

import subprocess

import pytest

from repowise.core.analysis.changed_lines import _parse_unified_diff, changed_lines


def test_parse_single_hunk_new_side_lines() -> None:
    diff = (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "index 111..222 100644\n"
        "--- a/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -10,0 +11,3 @@\n"
        "+one\n"
        "+two\n"
        "+three\n"
    )
    assert _parse_unified_diff(diff) == {"src/foo.py": {11, 12, 13}}


def test_parse_hunk_without_count_defaults_to_one() -> None:
    diff = "--- a/x.py\n+++ b/x.py\n@@ -4 +4 @@\n-old\n+new\n"
    assert _parse_unified_diff(diff) == {"x.py": {4}}


def test_parse_pure_deletion_yields_no_file() -> None:
    # ``+7,0`` = nothing on the new side; the file must not appear.
    diff = "--- a/gone.py\n+++ b/gone.py\n@@ -7,2 +7,0 @@\n-a\n-b\n"
    assert _parse_unified_diff(diff) == {}


def test_parse_deleted_file_skipped() -> None:
    diff = "--- a/dead.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n"
    assert _parse_unified_diff(diff) == {}


def test_parse_multiple_files_and_hunks() -> None:
    diff = (
        "--- a/a.py\n+++ b/a.py\n"
        "@@ -1 +1 @@\n-x\n+x2\n"
        "@@ -5,0 +6,2 @@\n+n1\n+n2\n"
        "--- a/b.py\n+++ b/b.py\n"
        "@@ -3,0 +4,1 @@\n+only\n"
    )
    assert _parse_unified_diff(diff) == {"a.py": {1, 6, 7}, "b.py": {4}}


# --- end-to-end against a real git repo -----------------------------------


def _git(cwd, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    f = tmp_path / "mod.py"
    f.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def test_changed_lines_range(git_repo) -> None:
    (git_repo / "mod.py").write_text("a = 1\nb = 22\nc = 3\nd = 4\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-qm", "edit")

    changed, label = changed_lines(str(git_repo), "HEAD~1..HEAD")
    assert label == "HEAD~1..HEAD"
    # Line 2 modified, line 4 added.
    assert changed == {"mod.py": {2, 4}}


def test_changed_lines_single_commit(git_repo) -> None:
    (git_repo / "mod.py").write_text("a = 1\nb = 2\nc = 3\ne = 5\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-qm", "add line")

    changed, label = changed_lines(str(git_repo), "HEAD")
    assert label == "HEAD"
    assert changed == {"mod.py": {4}}


def test_changed_lines_staged(git_repo) -> None:
    (git_repo / "mod.py").write_text("a = 1\nb = 2\nc = 3\nf = 6\n", encoding="utf-8")
    _git(git_repo, "add", "-A")  # stage, do not commit

    changed, label = changed_lines(str(git_repo))
    assert label == "staged changes"
    assert changed == {"mod.py": {4}}

    # Explicit --staged is the same as the no-arg default.
    assert changed_lines(str(git_repo), staged=True)[0] == {"mod.py": {4}}


def test_changed_lines_unknown_revision_raises(git_repo) -> None:
    with pytest.raises(ValueError):
        changed_lines(str(git_repo), "nope123..HEAD")
    with pytest.raises(ValueError):
        changed_lines(str(git_repo), "deadbeef")


def test_changed_lines_three_dot_diffs_from_merge_base(git_repo) -> None:
    # A branch edits line 2 while base moves on and edits line 3: the PR view
    # (three dots) reports only the branch's own line.
    _git(git_repo, "branch", "-M", "main")
    _git(git_repo, "switch", "-qc", "feat")
    (git_repo / "mod.py").write_text("a = 1\nb = 22\nc = 3\n", encoding="utf-8")
    _git(git_repo, "commit", "-qam", "feat edit")
    _git(git_repo, "switch", "-q", "main")
    (git_repo / "mod.py").write_text("a = 1\nb = 2\nc = 33\n", encoding="utf-8")
    _git(git_repo, "commit", "-qam", "base edit")

    changed, label = changed_lines(str(git_repo), "main...feat")
    assert label == "main...feat"
    assert changed == {"mod.py": {2}}
    # Two dots compares the tips, so base's own edit shows up too.
    assert changed_lines(str(git_repo), "main..feat")[0] == {"mod.py": {2, 3}}


def test_single_commit_at_a_shallow_boundary_raises(git_repo, tmp_path_factory) -> None:
    # Its parents are cut off, so git would diff against the empty tree and
    # report every line as changed.
    (git_repo / "mod.py").write_text("a = 1\nb = 2\nc = 3\nd = 4\n", encoding="utf-8")
    _git(git_repo, "commit", "-qam", "second")
    clone = tmp_path_factory.mktemp("shallow") / "c"
    _git(git_repo, "clone", "-q", "--depth", "1", git_repo.as_uri(), str(clone))

    with pytest.raises(ValueError, match="shallow"):
        changed_lines(str(clone), "HEAD")


def test_range_without_merge_base_raises_value_error(git_repo) -> None:
    _git(git_repo, "branch", "-M", "main")
    _git(git_repo, "checkout", "-q", "--orphan", "lonely")
    _git(git_repo, "commit", "-qm", "orphan")

    with pytest.raises(ValueError):
        changed_lines(str(git_repo), "main...lonely")
