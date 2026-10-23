"""Change detection when the indexed path is a subdirectory of the git repo.

Git reports paths relative to the repository root, while the index is keyed
relative to the directory that was indexed. For a module inside a larger
repository the two differ by a prefix, and an update that kept git's form
matched nothing in the index: it reported success and persisted no change.
"""

from __future__ import annotations

from pathlib import Path

import git as gitpython
import pytest

from repowise.core.ingestion.change_detector import (
    ChangeDetector,
    has_working_tree_changes,
)

MODULE = "svc/module"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo whose indexed module lives two directories down."""
    r = gitpython.Repo.init(tmp_path)
    with r.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    (tmp_path / MODULE).mkdir(parents=True)
    (tmp_path / "other").mkdir()
    (tmp_path / MODULE / "main.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (tmp_path / "other" / "x.py").write_text("def outside():\n    return 0\n", encoding="utf-8")
    r.index.add([f"{MODULE}/main.py", "other/x.py"])
    r.index.commit("init")
    r.close()
    return tmp_path


def _paths(diffs) -> set[str]:
    return {d.path for d in diffs}


def test_working_tree_paths_are_relative_to_the_module(repo: Path) -> None:
    module = repo / MODULE
    (module / "main.py").write_text("def alpha():\n    return 2\n", encoding="utf-8")

    diffs = ChangeDetector(module).get_working_tree_changes()

    assert _paths(diffs) == {"main.py"}
    assert diffs[0].status == "modified"
    assert diffs[0].new_parsed is not None
    assert diffs[0].new_parsed.file_info.path == "main.py"


def test_changes_outside_the_module_are_ignored(repo: Path) -> None:
    (repo / "other" / "x.py").write_text("def outside():\n    return 9\n", encoding="utf-8")
    (repo / "other" / "new.py").write_text("def fresh():\n    return 1\n", encoding="utf-8")

    detector = ChangeDetector(repo / MODULE)

    assert detector.get_working_tree_changes() == []
    assert has_working_tree_changes(repo / MODULE) is False


def test_untracked_files_inside_the_module_are_relative_to_it(repo: Path) -> None:
    module = repo / MODULE
    (module / "pkg").mkdir()
    (module / "pkg" / "added.py").write_text("def added():\n    return 1\n", encoding="utf-8")

    diffs = ChangeDetector(module).get_working_tree_changes()

    assert _paths(diffs) == {"pkg/added.py"}
    assert diffs[0].status == "added"
    assert has_working_tree_changes(module) is True


def test_commit_range_diff_is_scoped_and_relative(repo: Path) -> None:
    r = gitpython.Repo(repo)
    (repo / MODULE / "main.py").write_text("def alpha():\n    return 3\n", encoding="utf-8")
    (repo / "other" / "x.py").write_text("def outside():\n    return 4\n", encoding="utf-8")
    r.index.add([f"{MODULE}/main.py", "other/x.py"])
    r.index.commit("touch both")
    r.close()

    diffs = ChangeDetector(repo / MODULE).get_changed_files("HEAD~1", "HEAD")

    assert _paths(diffs) == {"main.py"}


def test_repo_root_behaviour_is_unchanged(repo: Path) -> None:
    (repo / "other" / "x.py").write_text("def outside():\n    return 5\n", encoding="utf-8")

    assert _paths(ChangeDetector(repo).get_working_tree_changes()) == {"other/x.py"}
