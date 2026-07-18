"""Docs-pointer preservation across workspace and index-only updates (issue #873).

Bug: workspace and index-only updates advanced ``last_sync_commit`` without
backfilling ``last_docs_commit``. Because ``repowise update --docs`` diffs from
``last_docs_commit``, a later docs run saw an empty diff and no-oped while the
wiki prose was actually stale.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.helpers import load_state, save_state
from repowise.cli.main import cli
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig

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


def _make_workspace(tmp_path: Path) -> Path:
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    repo = _make_git_repo(ws_root)
    WorkspaceConfig(
        version=1,
        repos=[RepoEntry(alias="my-repo", path="repo")],
    ).save(ws_root)
    return repo


def test_workspace_update_backfills_docs_pointer(tmp_path: Path) -> None:
    """A workspace update on a legacy state.json must backfill the docs pointer."""
    repo = _make_workspace(tmp_path)

    _index_full(repo)
    c0 = _git(repo, "rev-parse", "HEAD")

    # Simulate a state.json written before last_docs_commit existed.
    save_state(repo, {SYNC_POINTER_KEY: c0, "docs_enabled": True})
    assert DOCS_POINTER_KEY not in load_state(repo)

    c1 = _commit_change(repo, "c.py", "def gamma():\n    return 3\n", "add c.py")

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
    """Docs generated at C0, a workspace update walks last_sync_commit forward, then
    new source lands. The next --docs run must diff from C0, not the advanced pointer.
    """
    repo = _make_workspace(tmp_path)

    _index_full(repo)
    c0 = _git(repo, "rev-parse", "HEAD")
    save_state(repo, {SYNC_POINTER_KEY: c0, "docs_enabled": True})

    c1 = _commit_change(repo, "c.py", "def gamma():\n    return 3\n", "add c.py")
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

    from repowise.cli.commands.update_cmd.command import clear_update_queued, release_update_lock

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


def test_docs_early_exit_advances_docs_pointer(tmp_path: Path) -> None:
    """When a docs run finds no changed files, it must advance the docs pointer to
    HEAD (and, on legacy state, the sync pointer as well). An empty commit advances
    HEAD without touching any file, so the docs run diffs an empty set and takes the
    no-changed-files early exit.
    """
    repo = _make_git_repo(tmp_path)
    _index_full(repo)
    c0 = _git(repo, "rev-parse", "HEAD")

    # Legacy state: no docs pointer recorded yet.
    save_state(repo, {SYNC_POINTER_KEY: c0, "docs_enabled": True})

    _git(repo, "commit", "--allow-empty", "-m", "empty")
    c1 = _git(repo, "rev-parse", "HEAD")

    result = CliRunner().invoke(
        cli, ["update", str(repo), "--docs", "--provider", "mock", "--no-workspace"]
    )
    assert result.exit_code == 0, result.output
    assert "No changed files detected" in result.output, "expected an early exit"

    state = _state(repo)
    assert state.get(SYNC_POINTER_KEY) == c1, "sync pointer should advance to HEAD"
    assert state.get(DOCS_POINTER_KEY) == c1, "docs pointer should advance to HEAD on early exit"


def test_index_only_early_exit_advances_sync_pointer(tmp_path: Path) -> None:
    """An index-only run that finds no changed files must still advance
    last_sync_commit to HEAD (keep the freshness marker current on no-op syncs)
    and must leave the docs pointer untouched. Guards against the early-exit
    regressing into a state.json <-> DB head_commit split.
    """
    repo = _make_git_repo(tmp_path)
    _index_full(repo)
    c0 = _git(repo, "rev-parse", "HEAD")

    # docs disabled -> a plain `update` resolves to index-only mode.
    save_state(repo, {SYNC_POINTER_KEY: c0, DOCS_POINTER_KEY: c0, "docs_enabled": False})

    _git(repo, "commit", "--allow-empty", "-m", "empty")
    c1 = _git(repo, "rev-parse", "HEAD")

    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace"])
    assert result.exit_code == 0, result.output
    assert "No changed files detected" in result.output, "expected an early exit"

    state = _state(repo)
    assert state.get(SYNC_POINTER_KEY) == c1, "index-only early exit must advance the sync pointer"
    assert state.get(DOCS_POINTER_KEY) == c0, "index-only run must not advance the docs pointer"
