"""Tests for repowise.core.workspace.update — staleness and update orchestration."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from repowise.core.workspace.config import RepoEntry, WorkspaceConfig
from repowise.core.workspace.update import (
    RepoUpdateResult,
    check_repo_staleness,
    count_commits_between,
    get_head_commit,
    run_cross_repo_hooks,
    update_workspace,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_git_repo(tmp_path: Path, name: str) -> Path:
    """Create a real git repo with an initial commit."""
    repo = tmp_path / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo), capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo), capture_output=True,
    )
    (repo / "README.md").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=str(repo), capture_output=True,
    )
    return repo


def _add_commit(repo: Path, filename: str = "change.txt", msg: str = "update") -> str:
    """Add a file and commit. Returns the new commit SHA."""
    (repo / filename).write_text(f"change-{msg}")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=str(repo), capture_output=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo), capture_output=True, text=True,
    )
    return result.stdout.strip()


def _write_state(repo: Path, commit: str | None) -> None:
    """Write a state.json with the given commit."""
    state_dir = repo / ".repowise"
    state_dir.mkdir(parents=True, exist_ok=True)
    state = {}
    if commit:
        state["last_sync_commit"] = commit
    (state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")


# ---------------------------------------------------------------------------
# get_head_commit
# ---------------------------------------------------------------------------


class TestGetHeadCommit:
    def test_returns_sha(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path, "myrepo")
        head = get_head_commit(repo)
        assert head is not None
        assert len(head) == 40

    def test_non_git_returns_none(self, tmp_path: Path) -> None:
        assert get_head_commit(tmp_path) is None


# ---------------------------------------------------------------------------
# check_repo_staleness
# ---------------------------------------------------------------------------


class TestCheckRepoStaleness:
    def test_same_commit_not_stale(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path, "myrepo")
        head = get_head_commit(repo)
        is_stale, current, behind = check_repo_staleness(repo, head)
        assert is_stale is False
        assert current == head
        assert behind == 0

    def test_different_commit_stale(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path, "myrepo")
        old_head = get_head_commit(repo)
        _add_commit(repo, "file1.txt", "second commit")
        is_stale, current, behind = check_repo_staleness(repo, old_head)
        assert is_stale is True
        assert current != old_head
        assert behind == 1

    def test_no_stored_commit_stale(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path, "myrepo")
        is_stale, current, behind = check_repo_staleness(repo, None)
        assert is_stale is True
        assert current is not None

    def test_multiple_commits_behind(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path, "myrepo")
        old_head = get_head_commit(repo)
        _add_commit(repo, "a.txt", "commit 2")
        _add_commit(repo, "b.txt", "commit 3")
        _add_commit(repo, "c.txt", "commit 4")
        is_stale, current, behind = check_repo_staleness(repo, old_head)
        assert is_stale is True
        assert behind == 3

    def test_non_git_dir_not_stale(self, tmp_path: Path) -> None:
        is_stale, current, behind = check_repo_staleness(tmp_path, "abc123")
        assert is_stale is False
        assert current is None


# ---------------------------------------------------------------------------
# count_commits_between
# ---------------------------------------------------------------------------


class TestCountCommitsBetween:
    def test_counts_correctly(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path, "myrepo")
        old = get_head_commit(repo)
        _add_commit(repo, "a.txt")
        _add_commit(repo, "b.txt")
        new = get_head_commit(repo)
        assert count_commits_between(repo, old, new) == 2

    def test_zero_when_same(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path, "myrepo")
        head = get_head_commit(repo)
        assert count_commits_between(repo, head, head) == 0


# ---------------------------------------------------------------------------
# update_workspace
# ---------------------------------------------------------------------------


class TestUpdateWorkspace:
    def test_skips_up_to_date(self, tmp_path: Path) -> None:
        """Repos with same HEAD should be skipped."""
        repo = _make_git_repo(tmp_path, "backend")
        head = get_head_commit(repo)
        _write_state(repo, head)

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="backend", alias="backend", last_commit_at_index=head)],
            default_repo="backend",
        )
        ws_config.save(tmp_path)

        async def _run():
            return await update_workspace(tmp_path, ws_config)

        import asyncio
        results = asyncio.run(_run())
        assert len(results) == 1
        assert results[0].updated is False
        assert results[0].skipped_reason == "up_to_date"

    def test_detects_stale_repo(self, tmp_path: Path) -> None:
        """Repos with new commits should be detected as stale."""
        repo = _make_git_repo(tmp_path, "backend")
        old_head = get_head_commit(repo)
        _write_state(repo, old_head)
        _add_commit(repo, "new_file.txt")

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="backend", alias="backend", last_commit_at_index=old_head)],
            default_repo="backend",
        )
        ws_config.save(tmp_path)

        # Mock update_single_repo_index to avoid running the full pipeline
        mock_result = RepoUpdateResult(alias="backend", updated=True, file_count=10, symbol_count=50)

        async def _run():
            with patch(
                "repowise.core.workspace.update.update_single_repo_index",
                new_callable=AsyncMock,
                return_value=mock_result,
            ):
                return await update_workspace(tmp_path, ws_config)

        import asyncio
        results = asyncio.run(_run())
        updated = [r for r in results if r.updated]
        assert len(updated) == 1
        assert updated[0].alias == "backend"

    def test_repo_filter(self, tmp_path: Path) -> None:
        """--repo flag should only update the specified repo."""
        repo_a = _make_git_repo(tmp_path, "backend")
        repo_b = _make_git_repo(tmp_path, "frontend")
        old_a = get_head_commit(repo_a)
        old_b = get_head_commit(repo_b)
        _write_state(repo_a, old_a)
        _write_state(repo_b, old_b)
        _add_commit(repo_a, "a.txt")
        _add_commit(repo_b, "b.txt")

        ws_config = WorkspaceConfig(
            repos=[
                RepoEntry(path="backend", alias="backend", last_commit_at_index=old_a),
                RepoEntry(path="frontend", alias="frontend", last_commit_at_index=old_b),
            ],
            default_repo="backend",
        )
        ws_config.save(tmp_path)

        mock_result = RepoUpdateResult(alias="backend", updated=True, file_count=5, symbol_count=20)

        async def _run():
            with patch(
                "repowise.core.workspace.update.update_single_repo_index",
                new_callable=AsyncMock,
                return_value=mock_result,
            ):
                return await update_workspace(tmp_path, ws_config, repo_filter="backend")

        import asyncio
        results = asyncio.run(_run())
        # Only backend should appear (frontend filtered out)
        assert len(results) == 1
        assert results[0].alias == "backend"

    def test_invalid_repo_filter_raises(self, tmp_path: Path) -> None:
        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="backend", alias="backend")],
        )

        async def _run():
            return await update_workspace(tmp_path, ws_config, repo_filter="nonexistent")

        import asyncio
        with pytest.raises(ValueError, match="Unknown repo"):
            asyncio.run(_run())

    def test_not_indexed_skipped(self, tmp_path: Path) -> None:
        """Repos without .repowise/ should be skipped."""
        repo = _make_git_repo(tmp_path, "backend")
        # No .repowise/ dir, no state.json

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="backend", alias="backend")],
        )

        async def _run():
            return await update_workspace(tmp_path, ws_config)

        import asyncio
        results = asyncio.run(_run())
        assert len(results) == 1
        assert results[0].skipped_reason == "not_indexed"

    def test_dry_run(self, tmp_path: Path) -> None:
        """Dry run should not perform any updates."""
        repo = _make_git_repo(tmp_path, "backend")
        old_head = get_head_commit(repo)
        _write_state(repo, old_head)
        _add_commit(repo, "new.txt")

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="backend", alias="backend", last_commit_at_index=old_head)],
        )

        async def _run():
            return await update_workspace(tmp_path, ws_config, dry_run=True)

        import asyncio
        results = asyncio.run(_run())
        # Stale repos detected but not updated
        updated = [r for r in results if r.updated]
        assert len(updated) == 0


# ---------------------------------------------------------------------------
# Cross-repo hooks placeholder
# ---------------------------------------------------------------------------


class TestCrossRepoHooks:
    def test_runs_without_error(self, tmp_path: Path) -> None:
        """Placeholder hook should be a no-op."""
        ws_config = WorkspaceConfig(repos=[])

        import asyncio
        asyncio.run(run_cross_repo_hooks(ws_config, tmp_path, ["backend"]))


# ---------------------------------------------------------------------------
# RepoUpdateResult
# ---------------------------------------------------------------------------


class TestRepoUpdateResult:
    def test_defaults(self) -> None:
        r = RepoUpdateResult(alias="test", updated=False)
        assert r.file_count == 0
        assert r.symbol_count == 0
        assert r.error is None
        assert r.skipped_reason is None
