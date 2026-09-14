"""Read-only git ref queries, against a real repository built under tmp_path."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repowise.core.git_refs import (
    BranchRef,
    ahead_behind,
    changed_files,
    commit_file_sets,
    current_branch,
    default_base,
    list_branches,
    resolve,
)


def _run(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        check=True,
    )
    return proc.stdout


def _commit(repo: Path, name: str, body: str, message: str) -> None:
    (repo / name).write_text(body, encoding="utf-8")
    _run(repo, "add", name)
    _run(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """main with two commits, a feat branch forked after the first, remote refs."""
    root = tmp_path / "repo"
    root.mkdir()
    _run(root, "init", "-b", "main")
    _commit(root, "base.txt", "base\n", "base")

    _run(root, "checkout", "-b", "feat")
    _commit(root, "feat_one.txt", "one\n", "feat one")
    _commit(root, "feat_two.txt", "two\n", "feat two")

    _run(root, "checkout", "main")
    # A commit on main after the fork: three-dot diffs must not attribute it
    # to feat, and it makes feat one commit behind.
    _commit(root, "main_only.txt", "later\n", "main moves on")

    _run(root, "checkout", "-b", "other")
    _commit(root, "feat_one.txt", "other edit\n", "other touches a feat file")
    _run(root, "checkout", "main")

    _run(root, "update-ref", "refs/remotes/origin/main", "main")
    _run(root, "update-ref", "refs/remotes/origin/feat", "refs/heads/feat")
    _run(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    return root


class TestListBranches:
    def test_lists_local_and_remote_tips_newest_first(self, repo: Path) -> None:
        refs = list_branches(str(repo))
        names = [ref.name for ref in refs]
        assert set(names) == {"main", "feat", "other", "origin/main", "origin/feat"}
        dates = [ref.committed_at for ref in refs]
        assert dates == sorted(dates, reverse=True)
        assert all(isinstance(ref, BranchRef) for ref in refs)

    def test_symbolic_remote_head_is_skipped(self, repo: Path) -> None:
        assert "origin/HEAD" not in {ref.name for ref in list_branches(str(repo))}

    def test_is_remote_separates_tracking_refs(self, repo: Path) -> None:
        by_name = {ref.name: ref for ref in list_branches(str(repo))}
        assert by_name["origin/feat"].is_remote is True
        assert by_name["feat"].is_remote is False
        # The tracking ref was made to point at the local branch, same commit.
        assert by_name["origin/feat"].sha == by_name["feat"].sha

    def test_a_non_repository_yields_nothing(self, tmp_path: Path) -> None:
        assert list_branches(str(tmp_path / "nowhere_at_all")) == []


class TestCurrentBranch:
    def test_reports_the_checked_out_branch(self, repo: Path) -> None:
        assert current_branch(str(repo)) == "main"

    def test_detached_head_is_none(self, repo: Path) -> None:
        sha = _run(repo, "rev-parse", "HEAD").strip()
        _run(repo, "checkout", "--detach", sha)
        assert current_branch(str(repo)) is None

    def test_a_non_repository_is_none(self, tmp_path: Path) -> None:
        assert current_branch(str(tmp_path / "nowhere_at_all")) is None


class TestDefaultBase:
    def test_prefers_the_origin_head_symbolic_ref(self, repo: Path) -> None:
        assert default_base(str(repo)) == "origin/main"

    def test_falls_back_to_a_local_trunk(self, repo: Path) -> None:
        _run(repo, "symbolic-ref", "-d", "refs/remotes/origin/HEAD")
        assert default_base(str(repo)) == "main"

    def test_falls_back_to_head_when_no_trunk_exists(self, tmp_path: Path) -> None:
        root = tmp_path / "trunkless"
        root.mkdir()
        _run(root, "init", "-b", "topic")
        _commit(root, "a.txt", "a\n", "a")
        assert default_base(str(root)) == "HEAD"

    def test_a_non_repository_is_head(self, tmp_path: Path) -> None:
        assert default_base(str(tmp_path / "nowhere_at_all")) == "HEAD"


class TestChangedFiles:
    def test_three_dot_semantics_exclude_the_base_side(self, repo: Path) -> None:
        files = changed_files(str(repo), "main", "feat")
        assert files == ["feat_one.txt", "feat_two.txt"]
        assert "main_only.txt" not in files

    def test_the_same_revision_changes_nothing(self, repo: Path) -> None:
        assert changed_files(str(repo), "main", "main") == []

    def test_an_unknown_revision_yields_nothing(self, repo: Path) -> None:
        assert changed_files(str(repo), "main", "no-such-branch") == []

    def test_a_non_repository_yields_nothing(self, tmp_path: Path) -> None:
        assert changed_files(str(tmp_path / "nowhere_at_all"), "main", "feat") == []


class TestAheadBehind:
    def test_ahead_counts_commits_only_on_the_branch(self, repo: Path) -> None:
        assert ahead_behind(str(repo), "main", "feat") == (2, 1)

    def test_the_orientation_reverses_with_the_arguments(self, repo: Path) -> None:
        assert ahead_behind(str(repo), "feat", "main") == (1, 2)

    def test_an_unknown_revision_is_zero(self, repo: Path) -> None:
        assert ahead_behind(str(repo), "main", "no-such-branch") == (0, 0)

    def test_a_non_repository_is_zero(self, tmp_path: Path) -> None:
        assert ahead_behind(str(tmp_path / "nowhere_at_all"), "main", "feat") == (0, 0)


class TestResolve:
    def test_names_the_commit_a_branch_points_at(self, repo: Path) -> None:
        assert resolve(str(repo), "feat") == _run(repo, "rev-parse", "feat").strip()

    def test_an_unknown_revision_yields_nothing(self, repo: Path) -> None:
        assert resolve(str(repo), "no-such-branch") == ""

    def test_a_non_repository_yields_nothing(self, tmp_path: Path) -> None:
        assert resolve(str(tmp_path / "nowhere_at_all"), "main") == ""


class TestCommitFileSets:
    def test_a_range_yields_one_set_per_commit_oldest_first(self, tmp_path: Path) -> None:
        root = tmp_path / "ranged"
        root.mkdir()
        _run(root, "init", "-b", "main")
        _commit(root, "base.txt", "base\n", "base")
        _run(root, "checkout", "-b", "work")
        _commit(root, "one.txt", "1\n", "one")
        (root / "two.txt").write_text("2\n", encoding="utf-8")
        (root / "one.txt").write_text("1b\n", encoding="utf-8")
        _run(root, "add", "-A")
        _run(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "middle")
        _commit(root, "three.txt", "3\n", "three")

        assert commit_file_sets(str(root), "main..work") == [
            frozenset({"one.txt"}),
            frozenset({"one.txt", "two.txt"}),
            frozenset({"three.txt"}),
        ]

    def test_the_three_dot_form_reads_as_the_same_range(self, repo: Path) -> None:
        assert commit_file_sets(str(repo), "main...feat") == commit_file_sets(
            str(repo), "main..feat"
        )

    def test_a_single_revision_carries_nothing(self, repo: Path) -> None:
        assert commit_file_sets(str(repo), "HEAD") == []
        assert commit_file_sets(str(repo), None) == []

    def test_a_non_repository_yields_nothing(self, tmp_path: Path) -> None:
        assert commit_file_sets(str(tmp_path / "nowhere_at_all"), "main..feat") == []
