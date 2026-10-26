from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from repowise.core.pipeline.preprocessing import preprocessed_changes
from repowise.core.registry import RepositoryPreprocessors


def test_preprocessor_receives_resolved_root_and_returns_managed_paths(tmp_path):
    registry = RepositoryPreprocessors()
    callback = Mock(return_value=["docs/manual.md"])
    registry.register("documents", callback)
    assert registry.run(tmp_path) == frozenset({"docs/manual.md"})
    callback.assert_called_once_with(tmp_path.resolve())


def test_empty_registry_is_noop(tmp_path):
    assert RepositoryPreprocessors().run(tmp_path) == frozenset()


def test_registration_is_idempotent_by_name(tmp_path):
    registry = RepositoryPreprocessors()
    callback = Mock(return_value=[])
    registry.register("documents", callback)
    registry.register("documents", callback)
    registry.run(tmp_path)
    callback.assert_called_once()


@pytest.mark.parametrize(
    "path", ["../escape.md", "/tmp/escape.md", ".", ".git/file", ".repowise/file"]
)
def test_invalid_managed_paths_fail_loudly(tmp_path, path):
    registry = RepositoryPreprocessors()
    registry.register("documents", lambda root: [path])
    with pytest.raises(RuntimeError, match=r"documents.*failed"):
        registry.run(tmp_path)


def test_failure_stops_subsequent_preprocessors(tmp_path):
    registry = RepositoryPreprocessors()
    registry.register("broken", Mock(side_effect=ValueError("invalid PDF")))
    later = Mock(return_value=[])
    registry.register("later", later)
    with pytest.raises(RuntimeError, match=r"broken.*invalid PDF"):
        registry.run(tmp_path)
    later.assert_not_called()


def test_symlink_escape_is_rejected(tmp_path):
    (tmp_path / "escape").symlink_to(tmp_path.parent, target_is_directory=True)
    registry = RepositoryPreprocessors()
    registry.register("documents", lambda root: ["escape/file.md"])
    with pytest.raises(RuntimeError, match="escapes repository"):
        registry.run(tmp_path)


def test_generated_diffs_exclude_unrelated_work(tmp_path):
    managed = "docs/manual.md"
    (tmp_path / "docs").mkdir()
    (tmp_path / managed).write_text("Text")
    generated = SimpleNamespace(path=managed)
    unrelated = SimpleNamespace(path="app.py")
    detector = Mock(repo_path=tmp_path)
    detector.get_working_tree_changes.return_value = [generated, unrelated]
    detector.stale_working_tree_diffs.return_value = []
    diffs, dirty = preprocessed_changes(detector, [managed], [])
    assert diffs == [generated]
    assert dirty == [managed]


def test_removed_generated_file_is_rediffed_without_a_git_commit(tmp_path):
    detector = Mock(repo_path=tmp_path)
    detector.get_working_tree_changes.return_value = []
    deleted = SimpleNamespace(path="docs/manual.md", status="deleted")
    detector.stale_working_tree_diffs.return_value = [deleted]
    diffs, _ = preprocessed_changes(detector, ["docs/manual.md"], [])
    assert diffs == [deleted]
    detector.stale_working_tree_diffs.assert_called_once_with({"docs/manual.md"}, set())


def test_no_managed_paths_preserves_commit_anchored_behaviour(tmp_path):
    detector = Mock(repo_path=tmp_path)
    assert preprocessed_changes(detector, [], []) == ([], [])
    detector.get_working_tree_changes.assert_not_called()


def test_plugin_discovery_precedes_preprocessing(monkeypatch, tmp_path):
    from repowise.core.registry import plugins, preprocess_repository, preprocessors

    registry = RepositoryPreprocessors()
    monkeypatch.setattr(preprocessors, "repository_preprocessors", registry)

    def load():
        registry.register("discovered", lambda root: ["docs/discovered.md"])

    monkeypatch.setattr(plugins, "load_plugins", load)
    assert preprocess_repository(tmp_path) == frozenset({"docs/discovered.md"})
