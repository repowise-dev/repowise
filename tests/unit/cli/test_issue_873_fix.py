"""Verification suite for issue #873.

Bug: Workspace updates advanced `last_sync_commit` without backfilling `last_docs_commit`.
Because `repowise update --docs` uses `last_docs_commit` as its diff base, a later docs run saw an empty diff.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.helpers import save_state, load_state
from repowise.cli.main import cli
from repowise.core.workspace.config import WorkspaceConfig, RepoEntry


DOCS_POINTER_KEY = "last_docs_commit"
SYNC_POINTER_KEY = "last_sync_commit"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)
    return result.stdout.strip()


def _make_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.py").write_text("def alpha():\n    return 1\n")
    (repo / "b.py").write_text("from a import alpha\n\n\ndef beta():\n    return alpha() + 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _index_full(repo: Path) -> None:
    import asyncio
    from repowise.core.pipeline.full_index import index_repo_full
    asyncio.run(index_repo_full(repo))


def _state(repo: Path) -> dict:
    return json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))


def _commit_change(repo: Path, filename: str, content: str, message: str) -> str:
    (repo / filename).write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def test_workspace_update_backfills_docs_pointer(tmp_path: Path) -> None:
    """A workspace update on a legacy state.json must backfill the docs pointer."""
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    
    repo = _make_git_repo(ws_root)
    
    # Initialize workspace config
    ws_config = WorkspaceConfig(
        version=1,
        repos=[RepoEntry(alias="my-repo", path="repo")],
    )
    ws_config.save(ws_root)

    _index_full(repo)
    c0 = _git(repo, "rev-parse", "HEAD")
    
    # Simulating a state.json missing last_docs_commit
    save_state(repo, {SYNC_POINTER_KEY: c0, "docs_enabled": True})
    assert DOCS_POINTER_KEY not in load_state(repo)

    c1 = _commit_change(repo, "c.py", "def gamma():\n    return 3\n", "add c.py")

    # Run workspace update
    import os
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        result = CliRunner().invoke(cli, ["update", "--workspace"])
    finally:
        os.chdir(old_cwd)
    assert result.exit_code == 0, result.output

    state = _state(repo)
    assert state[SYNC_POINTER_KEY] == c1, "sync pointer should advance to new commit"
    assert state.get(DOCS_POINTER_KEY) == c0, (
        "workspace update must backfill docs pointer from the old sync pointer"
    )


def test_stale_prose_is_reachable_after_workspace_update(tmp_path: Path) -> None:
    """Docs generated at C0, then workspace update walks last_sync_commit forward, 
    then new source changes land. The next --docs run must diff from C0, not from the 
    workspace-advanced pointer.
    """
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    repo = _make_git_repo(ws_root)
    
    ws_config = WorkspaceConfig(
        version=1,
        repos=[RepoEntry(alias="my-repo", path="repo")],
    )
    ws_config.save(ws_root)

    _index_full(repo)
    c0 = _git(repo, "rev-parse", "HEAD")
    save_state(repo, {SYNC_POINTER_KEY: c0, "docs_enabled": True})
    
    # Simulate a workspace update with no source changes or a minor change
    c1 = _commit_change(repo, "c.py", "def gamma():\n    return 3\n", "add c.py")
    import os
    old_cwd = os.getcwd()
    try:
        os.chdir(str(repo))
        result = CliRunner().invoke(cli, ["update", "--workspace"])
    finally:
        os.chdir(old_cwd)
    assert result.exit_code == 0, result.output
    assert _state(repo)[SYNC_POINTER_KEY] == c1
    assert _state(repo)[DOCS_POINTER_KEY] == c0

    # A second source change that must still be picked up by the next docs run.
    c2 = _commit_change(repo, "d.py", "def delta():\n    return 4\n", "add d.py")

    # Clear locks
    from repowise.cli.commands.update_cmd.command import release_update_lock, clear_update_queued
    release_update_lock(repo)
    clear_update_queued(repo)

    result = CliRunner().invoke(
        cli, ["update", str(repo), "--docs", "--provider", "mock", "--no-workspace"]
    )
    assert result.exit_code == 0, result.output

    assert "No changed files detected" not in result.output, (
        "docs run used the workspace-advanced pointer as its diff base and saw an empty diff"
    )

    state = _state(repo)
    assert state.get(DOCS_POINTER_KEY) == c2, "docs pointer should now reach HEAD"
