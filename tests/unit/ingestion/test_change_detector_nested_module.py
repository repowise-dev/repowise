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


def test_repowe_generated_editor_file_inside_module_is_ignored(repo: Path) -> None:
    module = repo / MODULE
    from repowise.core.generation.editor_files.base import BaseEditorFileGenerator
    from repowise.core.generation.editor_files.claude_md import ClaudeMdGenerator

    generated = module / ".claude" / "CLAUDE.md"
    generated.parent.mkdir(parents=True)
    tag = ClaudeMdGenerator.marker_tag
    generated.write_text(
        ClaudeMdGenerator.user_placeholder
        + BaseEditorFileGenerator.MARKER_START_FMT.format(tag=tag)
        + "\nLast indexed: today\n"
        + BaseEditorFileGenerator.MARKER_END_FMT.format(tag=tag)
        + "\n",
        encoding="utf-8",
    )

    detector = ChangeDetector(module)

    assert detector.get_working_tree_changes() == []
    assert has_working_tree_changes(module) is False


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


def test_a_sibling_sharing_the_prefix_is_outside_the_module(tmp_path: Path) -> None:
    # Indexing svc/mod must not pick up svc/module, whose path starts with it.
    r = gitpython.Repo.init(tmp_path)
    with r.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    for d in ("svc/mod", "svc/module"):
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    r.index.add(["svc/mod/a.py", "svc/module/a.py"])
    r.index.commit("init")
    (tmp_path / "svc/module/a.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    r.index.add(["svc/module/a.py"])
    r.index.commit("touch the sibling")
    r.close()

    (tmp_path / "svc/module/a.py").write_text("def a():\n    return 3\n", encoding="utf-8")
    (tmp_path / "svc/module/new.py").write_text("def n():\n    return 1\n", encoding="utf-8")
    module = tmp_path / "svc" / "mod"
    detector = ChangeDetector(module)

    assert detector.get_working_tree_changes() == []
    assert detector.get_changed_files("HEAD~1", "HEAD") == []
    assert has_working_tree_changes(module) is False
