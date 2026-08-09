"""Working-tree change detection — what ``repowise watch`` runs on.

``get_changed_files`` diffs commit to commit, so a repo whose only changes are
uncommitted reads as unchanged: the watcher fired an update per file save and
every one of them was a no-op. These cover the source that answers the other
question — what is on disk that ``HEAD`` does not have.
"""

from __future__ import annotations

from pathlib import Path

import git as gitpython
import pytest

from repowise.core.ingestion.change_detector import (
    ChangeDetector,
    FileDiff,
    has_working_tree_changes,
    merge_file_diffs,
)
from repowise.core.ingestion.traverser import is_candidate_source_path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A one-commit git repo with two python files."""
    r = gitpython.Repo.init(tmp_path)
    with r.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    (tmp_path / "main.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (tmp_path / "util.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    r.index.add(["main.py", "util.py"])
    r.index.commit("init")
    r.close()
    return tmp_path


def _by_path(diffs: list[FileDiff]) -> dict[str, FileDiff]:
    return {d.path: d for d in diffs}


class TestGetWorkingTreeChanges:
    def test_clean_tree_reports_nothing(self, repo: Path) -> None:
        assert ChangeDetector(repo).get_working_tree_changes() == []

    def test_unstaged_edit_is_a_modification(self, repo: Path) -> None:
        (repo / "main.py").write_text(
            "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n", encoding="utf-8"
        )

        diffs = _by_path(ChangeDetector(repo).get_working_tree_changes())

        assert diffs["main.py"].status == "modified"
        # The new side is read from disk, so the symbol the user just typed
        # is in the diff — this is the content the index has to pick up.
        assert diffs["main.py"].new_parsed is not None
        assert {s.name for s in diffs["main.py"].new_parsed.symbols} == {"alpha", "beta"}
        assert [s.name for s in diffs["main.py"].symbol_diff.added] == ["beta"]

    def test_staged_edit_is_reported_too(self, repo: Path) -> None:
        (repo / "main.py").write_text("def gamma():\n    return 3\n", encoding="utf-8")
        r = gitpython.Repo(repo)
        r.index.add(["main.py"])
        r.close()

        diffs = _by_path(ChangeDetector(repo).get_working_tree_changes())

        assert diffs["main.py"].status == "modified"

    def test_untracked_source_file_is_an_addition(self, repo: Path) -> None:
        (repo / "brand_new.py").write_text("def fresh():\n    return 4\n", encoding="utf-8")

        diffs = _by_path(ChangeDetector(repo).get_working_tree_changes())

        assert diffs["brand_new.py"].status == "added"
        assert diffs["brand_new.py"].old_parsed is None
        assert [s.name for s in diffs["brand_new.py"].symbol_diff.added] == ["fresh"]

    def test_deleted_file_is_a_deletion(self, repo: Path) -> None:
        (repo / "util.py").unlink()

        diffs = _by_path(ChangeDetector(repo).get_working_tree_changes())

        assert diffs["util.py"].status == "deleted"
        assert diffs["util.py"].new_parsed is None

    def test_untracked_paths_the_index_would_skip_are_dropped(self, repo: Path) -> None:
        # repowise's own state dir is the one that matters: it changes on every
        # update, so letting it through would make each update schedule the next.
        (repo / ".repowise").mkdir()
        (repo / ".repowise" / "state.json").write_text("{}", encoding="utf-8")
        (repo / "node_modules" / "dep").mkdir(parents=True)
        (repo / "node_modules" / "dep" / "index.js").write_text("x", encoding="utf-8")
        (repo / "app.log").write_text("noise", encoding="utf-8")

        paths = {d.path for d in ChangeDetector(repo).get_working_tree_changes()}

        assert paths == set()

    def test_gitignored_files_never_appear(self, repo: Path) -> None:
        (repo / ".gitignore").write_text("secret.py\n", encoding="utf-8")
        (repo / "secret.py").write_text("KEY = 'x'\n", encoding="utf-8")
        (repo / "visible.py").write_text("KEY = 'y'\n", encoding="utf-8")

        paths = {d.path for d in ChangeDetector(repo).get_working_tree_changes()}

        assert paths == {"visible.py"}

    def test_non_git_directory_is_empty_not_an_error(self, tmp_path: Path) -> None:
        (tmp_path / "loose.py").write_text("x = 1\n", encoding="utf-8")

        assert ChangeDetector(tmp_path).get_working_tree_changes() == []


class TestHasWorkingTreeChanges:
    def test_false_on_a_clean_tree(self, repo: Path) -> None:
        assert has_working_tree_changes(repo) is False

    def test_true_for_a_tracked_edit(self, repo: Path) -> None:
        (repo / "main.py").write_text("def alpha():\n    return 99\n", encoding="utf-8")

        assert has_working_tree_changes(repo) is True

    def test_true_for_an_untracked_source_file(self, repo: Path) -> None:
        (repo / "new.py").write_text("x = 1\n", encoding="utf-8")

        assert has_working_tree_changes(repo) is True

    def test_ignorable_untracked_noise_does_not_count(self, repo: Path) -> None:
        (repo / ".repowise").mkdir()
        (repo / ".repowise" / "state.json").write_text("{}", encoding="utf-8")

        assert has_working_tree_changes(repo) is False

    def test_false_on_a_non_git_directory(self, tmp_path: Path) -> None:
        assert has_working_tree_changes(tmp_path) is False


def _diff(path: str, status: str) -> FileDiff:
    return FileDiff(
        path=path,
        status=status,  # type: ignore[arg-type]
        old_path=None,
        old_parsed=None,
        new_parsed=None,
        symbol_diff=None,
    )


class TestMergeFileDiffs:
    def test_first_source_wins_for_a_path_in_both(self) -> None:
        commit_side = _diff("a.py", "modified")
        working_side = _diff("a.py", "added")

        merged = merge_file_diffs([commit_side], [working_side])

        # The commit-range entry is baselined at the last indexed commit; the
        # working-tree one only reaches back to HEAD.
        assert merged == [commit_side]

    def test_paths_from_either_source_are_kept(self) -> None:
        merged = merge_file_diffs([_diff("a.py", "modified")], [_diff("b.py", "added")])

        assert {d.path for d in merged} == {"a.py", "b.py"}


class TestIsCandidateSourcePath:
    @pytest.mark.parametrize(
        "path",
        [
            "src/app.py",
            "packages/ui/index.ts",
            "Dockerfile",
            "README.md",
            "deep/nested/mod.go",
        ],
    )
    def test_source_paths(self, path: str) -> None:
        assert is_candidate_source_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            ".repowise/state.json",
            ".git/index",
            "node_modules/left-pad/index.js",
            "dist/bundle.js",
            "build/out.js",
            ".venv/lib/x.py",
            "package-lock.json",
            "uv.lock",
            "app.pyc",
            "notes.txt.bak",
            "logo.png",
        ],
    )
    def test_non_source_paths(self, path: str) -> None:
        assert is_candidate_source_path(path) is False

    def test_windows_separators_are_understood(self) -> None:
        assert is_candidate_source_path(r"src\app.py") is True
        assert is_candidate_source_path(r".repowise\state.json") is False

    def test_empty_path(self) -> None:
        assert is_candidate_source_path("") is False
