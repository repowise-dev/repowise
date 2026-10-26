from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from repowise.cli.commands.update_cmd import command


def test_plain_update_collects_only_preprocessor_paths(monkeypatch, tmp_path: Path):
    from repowise.core import ingestion

    generated = SimpleNamespace(path="docs/manual.md")
    detector = Mock(repo_path=tmp_path)
    detector.get_working_tree_changes.return_value = [
        generated,
        SimpleNamespace(path="unrelated.py"),
    ]
    detector.stale_working_tree_diffs.return_value = []
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/manual.md").write_text("Manual")
    monkeypatch.setattr(ingestion, "ChangeDetector", lambda root: detector)
    opts = SimpleNamespace(include_working_tree=False, dry_run=False)
    run = SimpleNamespace(repo_path=tmp_path, state={}, working_tree_diffs=[])
    command._collect_working_tree(opts, run, frozenset({"docs/manual.md"}))
    assert run.working_tree_diffs == [generated]
    assert run.state["preprocessed_paths"] == ["docs/manual.md"]


def test_dry_run_does_not_refresh_preprocessed_files(monkeypatch, tmp_path):
    from repowise.core import ingestion

    detector = Mock(repo_path=tmp_path)
    monkeypatch.setattr(ingestion, "ChangeDetector", lambda root: detector)
    opts = SimpleNamespace(include_working_tree=False, dry_run=True)
    run = SimpleNamespace(repo_path=tmp_path, state={}, working_tree_diffs=[])
    command._collect_working_tree(opts, run, frozenset({"docs/manual.md"}))
    detector.get_working_tree_changes.assert_not_called()
    assert run.state == {}
