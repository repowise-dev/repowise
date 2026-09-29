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


def test_working_tree_from_a_base_is_everything_a_push_brings(git_repo) -> None:
    # The branch committed line 2 and left line 4 uncommitted; base moved line 3.
    _git(git_repo, "branch", "-M", "main")
    _git(git_repo, "switch", "-qc", "feat")
    (git_repo / "mod.py").write_text("a = 1\nb = 22\nc = 3\n", encoding="utf-8")
    _git(git_repo, "commit", "-qam", "feat edit")
    _git(git_repo, "switch", "-q", "main")
    (git_repo / "mod.py").write_text("a = 1\nb = 2\nc = 33\n", encoding="utf-8")
    _git(git_repo, "commit", "-qam", "base edit")
    _git(git_repo, "switch", "-q", "feat")
    (git_repo / "mod.py").write_text("a = 1\nb = 22\nc = 3\nd = 4\n", encoding="utf-8")

    (git_repo / "new.py").write_text("x = 1\ny = 2", encoding="utf-8")  # untracked

    changed, label = changed_lines(str(git_repo), working_tree=True, base="main")
    assert label == "main...working tree"
    # An untracked file is new code the push brings: every line changed.
    assert changed == {"mod.py": {2, 4}, "new.py": {1, 2}}
    # Without a base it stays the uncommitted edit alone.
    assert changed_lines(str(git_repo), working_tree=True) == ({"mod.py": {4}}, "working tree")
    # A base that does not resolve falls back to the uncommitted edit, still
    # with the new files a push would bring.
    assert changed_lines(str(git_repo), working_tree=True, base="nope") == (
        {"mod.py": {4}, "new.py": {1, 2}},
        "working tree",
    )


def test_map_old_line_follows_insertions_deletions_and_rewrites() -> None:
    from repowise.core.analysis.changed_lines import map_old_line, parse_unified_diff

    diff = (
        "--- a/m.py\n+++ b/m.py\n"
        "@@ -0,0 +1,2 @@\n+n1\n+n2\n"  # two lines inserted at the top
        "@@ -5,2 +7,0 @@\n-x\n-y\n"  # lines 5-6 deleted
        "@@ -10 +10,3 @@\n-z\n+a\n+b\n+c\n"  # line 10 rewritten as three
    )
    hunks = parse_unified_diff(diff)["m.py"].hunks
    assert map_old_line(hunks, 3) == 5
    assert map_old_line(hunks, 8) == 8
    assert (map_old_line(hunks, 10), map_old_line(hunks, 10, end=True)) == (10, 12)
    assert map_old_line(hunks, 20) == 22


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


def test_split_revspec() -> None:
    from repowise.core.analysis.change_risk.features import split_revspec

    assert split_revspec("a..b") == ("a", "..", "b")
    assert split_revspec("a...b") == ("a", "...", "b")
    assert split_revspec("HEAD~3..") == ("HEAD~3", "..", "HEAD")
    assert split_revspec("..HEAD") == ("HEAD", "..", "HEAD")
    assert split_revspec("HEAD") is None


def test_quoted_non_ascii_header_paths_are_decoded() -> None:
    # git quotes a non-ASCII path and escapes its UTF-8 bytes in octal.
    diff = (
        r'diff --git "a/caf\303\251.py" "b/caf\303\251.py"' "\n"
        r'--- "a/caf\303\251.py"' "\n"
        r'+++ "b/caf\303\251.py"' "\n"
        "@@ -1 +1 @@\n"
        "-a = 1\n"
        "+a = 2\n"
    )
    assert _parse_unified_diff(diff) == {"café.py": {1}}


def test_real_non_ascii_path_matches_its_tree_key(git_repo) -> None:
    (git_repo / "café.py").write_text("a = 1\n", encoding="utf-8")
    _git(git_repo, "add", "café.py")
    _git(git_repo, "commit", "-qm", "add")
    (git_repo / "café.py").write_text("a = 2\n", encoding="utf-8")
    _git(git_repo, "commit", "-qam", "edit")

    assert changed_lines(str(git_repo), "HEAD")[0] == {"café.py": {1}}


def test_change_health_refuses_a_shallow_boundary_commit(git_repo, tmp_path_factory) -> None:
    from repowise.core.analysis.change_health.sources import GitRevisionSource

    (git_repo / "mod.py").write_text("a = 1\nb = 2\nc = 3\nd = 4\n", encoding="utf-8")
    _git(git_repo, "commit", "-qam", "second")
    clone = tmp_path_factory.mktemp("shallow") / "c"
    _git(git_repo, "clone", "-q", "--depth", "1", git_repo.as_uri(), str(clone))

    with pytest.raises(ValueError, match="shallow"):
        GitRevisionSource(str(clone)).resolve("HEAD")
    # A real root commit in a full clone still diffs against the empty tree.
    root = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert GitRevisionSource(str(git_repo)).resolve(root).base_sha
