"""Doctor reports the post-commit hook and probes that .repowise takes a write.

init installs the hook by default, so doctor is where a hook that silently
failed to install, or was deleted, becomes visible. The hook row goes
through hooks.status, which asks git for the real hooks directory. The
directory row used to test existence only, which read OK for the one state
that stops the MCP server from starting at all.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from repowise.cli import hooks
from repowise.cli.commands.doctor_cmd import repo_checks


def _git_repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


def test_hook_row_reads_installed_through_hooks_status(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    hooks.install(repo)
    row = repo_checks._autosync_hook_check(repo)
    assert row.ok
    assert row.detail.startswith("installed")


def test_hook_row_names_the_install_command_when_absent(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    row = repo_checks._autosync_hook_check(repo)
    assert row.ok
    assert "not installed" in row.detail
    assert "repowise hook install" in row.detail


def test_hook_row_follows_core_hooks_path(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    subprocess.run(["git", "config", "core.hooksPath", "my-hooks"], cwd=repo, check=True)
    hooks.install(repo)
    assert (repo / "my-hooks" / "post-commit").exists()
    assert repo_checks._autosync_hook_check(repo).detail.startswith("installed")


def test_hook_row_never_fails_doctor(tmp_path: Path, monkeypatch) -> None:
    def _boom(_p: Path) -> str:
        raise RuntimeError("git exploded")

    monkeypatch.setattr(hooks, "status", _boom)
    row = repo_checks._autosync_hook_check(tmp_path)
    assert row.ok
    assert "git exploded" in row.detail


def test_directory_row_fails_when_missing(tmp_path: Path) -> None:
    row = repo_checks._repowise_dir_check(tmp_path / ".repowise")
    assert not row.ok
    assert "repowise init" in row.detail


def test_directory_row_fails_when_it_is_not_a_directory(tmp_path: Path) -> None:
    target = tmp_path / ".repowise"
    target.write_text("not a directory\n")
    row = repo_checks._repowise_dir_check(target)
    assert not row.ok
    assert "not writable" in row.detail
    assert "permissions" in row.detail


def test_directory_row_passes_and_leaves_no_probe_behind(tmp_path: Path) -> None:
    target = tmp_path / ".repowise"
    target.mkdir()
    row = repo_checks._repowise_dir_check(target)
    assert row.ok
    assert row.detail == str(target)
    assert list(target.iterdir()) == []
