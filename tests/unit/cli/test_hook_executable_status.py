"""Regression tests for reporting non-executable post-commit hooks."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repowise.cli import hooks


def _git_repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


def test_status_reports_non_executable_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path)
    hooks.install(repo)
    monkeypatch.setattr(hooks, "_is_executable", lambda _path: False)

    assert hooks.status(repo) == "installed but not executable"


def test_install_reports_when_permissions_cannot_be_repaired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path)
    original_chmod = Path.chmod
    monkeypatch.setattr(hooks.os, "name", "posix")
    monkeypatch.setattr(hooks, "_is_executable", lambda _path: False)

    def fail_chmod(path: Path, mode: int) -> None:
        if path.name == "post-commit":
            raise OSError("read-only hooks directory")
        original_chmod(path, mode)

    monkeypatch.setattr(Path, "chmod", fail_chmod)

    assert hooks.install(repo) == "installed but not executable"
