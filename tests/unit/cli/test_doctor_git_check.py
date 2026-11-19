"""Doctor's git row honours GIT_DIR / GIT_WORK_TREE without trusting the cwd (#1931).

In a linked worktree or a container the explicit-path constructor can fail
where the environment names the repo. The fallback must answer for
``repo_path``: ``Repo()`` with no variables set searches up from the cwd, so
a doctor run from inside any checkout would report a path that is not a repo
as healthy.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repowise.cli.commands.doctor_cmd._checks.core import _git_check


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


@pytest.fixture(autouse=True)
def _no_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(name, raising=False)


def test_cwd_inside_another_repo_is_not_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _git_repo(tmp_path / "other")
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    monkeypatch.chdir(other)

    row = _git_check(not_a_repo)

    assert not row.ok


def test_git_dir_naming_the_work_tree_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The explicit constructor finds no .git under work_tree; the variables do.
    store = _git_repo(tmp_path / "store")
    work_tree = tmp_path / "checkout"
    work_tree.mkdir()
    monkeypatch.setenv("GIT_DIR", str(store / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(work_tree))

    row = _git_check(work_tree)

    assert row.ok


def test_git_dir_for_a_different_tree_is_not_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _git_repo(tmp_path / "store")
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    monkeypatch.setenv("GIT_DIR", str(store / ".git"))

    row = _git_check(not_a_repo)

    assert not row.ok
