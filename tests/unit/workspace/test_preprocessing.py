from unittest.mock import AsyncMock, Mock

import pytest

from repowise.core.registry import RepositoryPreprocessors, preprocessors
from repowise.core.workspace.config import RepoEntry
from repowise.core.workspace.update import workspace_pass


@pytest.mark.asyncio
async def test_workspace_selection_preprocesses_and_retains_removed_paths(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    registry = RepositoryPreprocessors()
    registry.register("documents", lambda path: ["docs/deleted.md"])
    monkeypatch.setattr(preprocessors, "repository_preprocessors", registry)
    monkeypatch.setattr(workspace_pass, "read_repo_state", lambda path: {})
    monkeypatch.setattr(workspace_pass, "_repo_is_stale", AsyncMock(return_value=(False, "head")))
    managed = {}
    selected = await workspace_pass._select_stale_repos(
        tmp_path, [RepoEntry(alias="repo", path="repo")], False, [], preprocessed=managed
    )
    assert len(selected) == 1
    assert managed[root] == frozenset({"docs/deleted.md"})


@pytest.mark.asyncio
async def test_workspace_dry_run_never_invokes_preprocessors(monkeypatch, tmp_path):
    (tmp_path / "repo").mkdir()
    registry = RepositoryPreprocessors()
    prepare = Mock(return_value=[])
    registry.register("documents", prepare)
    monkeypatch.setattr(preprocessors, "repository_preprocessors", registry)
    monkeypatch.setattr(workspace_pass, "_repo_is_stale", AsyncMock(return_value=(True, "head")))
    await workspace_pass._select_stale_repos(
        tmp_path, [RepoEntry(alias="repo", path="repo")], False, [], dry_run=True
    )
    prepare.assert_not_called()
