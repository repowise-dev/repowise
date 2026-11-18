"""Matching a requested path to an indexed repo by git identity, never by name."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from repowise.core.git_head import find_checkout, match_checkout
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig
from repowise.core.workspace.registry import RepoRegistry

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(cwd: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e.x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e.x",
    }
    subprocess.run(
        ["git", "-c", "core.hooksPath=", *args], cwd=cwd, check=True, capture_output=True, env=env
    )


def _repo(path: Path) -> Path:
    (path / "src").mkdir(parents=True)
    (path / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
    _git(path, "init", "-q")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _repo(tmp_path / "repo")


def test_worktree_and_subdirectory_match(repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "x", str(wt))

    inside = match_checkout(repo / "src", repo)
    assert inside is not None and inside.same_checkout

    other = match_checkout(wt / "src", repo)
    assert other is not None and not other.same_checkout
    assert other.checkout == wt.resolve()


def test_forged_worktree_pointer_is_not_the_repo(repo: Path, tmp_path: Path) -> None:
    # A .git file naming the repo's git dir, which git itself never wrote.
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / ".git").write_text(f"gitdir: {repo / '.git'}\n", encoding="utf-8")
    assert match_checkout(fake, repo) is None

    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "x", str(wt))
    forged = tmp_path / "forged"
    forged.mkdir()
    pointer = (wt / ".git").read_text(encoding="utf-8")
    (forged / ".git").write_text(pointer, encoding="utf-8")
    assert find_checkout(forged) is None
    assert match_checkout(forged, repo) is None


def test_another_repository_never_matches(repo: Path, tmp_path: Path) -> None:
    other = _repo(tmp_path / "other")
    assert match_checkout(other, repo) is None
    # A separate repository nested inside the served one is still separate.
    nested = _repo(repo / "vendor" / "lib")
    assert match_checkout(nested, repo) is None
    assert match_checkout(nested / "src", repo) is None


def test_outside_git_only_containment_matches(tmp_path: Path) -> None:
    served = tmp_path / "plain"
    (served / "sub").mkdir(parents=True)
    assert match_checkout(served / "sub", served) is not None
    assert match_checkout(tmp_path, served) is None


def test_indexed_subdirectory_maps_to_the_same_place_in_a_worktree(
    repo: Path, tmp_path: Path
) -> None:
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "x", str(wt))
    found = match_checkout(wt / "src", repo / "src")
    assert found is not None and found.checkout == (wt / "src").resolve()
    # The worktree root is outside the indexed directory.
    assert match_checkout(wt, repo / "src") is None


def test_registry_resolves_a_worktree_path_to_its_alias(repo: Path, tmp_path: Path) -> None:
    other = _repo(tmp_path / "other")
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "x", str(wt))
    config = WorkspaceConfig(
        repos=[
            RepoEntry(path="repo", alias="main", is_primary=True),
            RepoEntry(path="other", alias="side"),
        ]
    )
    registry = RepoRegistry(tmp_path, config)
    assert registry.resolve_repo_param(str(wt / "src")) == "main"
    assert registry.resolve_repo_param(str(other / "src")) == "side"
    with pytest.raises(ValueError, match="Unknown repo"):
        registry.resolve_repo_param(str(tmp_path / "elsewhere"))
    # Relative strings keep their old meaning: an alias or a config path.
    with pytest.raises(ValueError, match="Unknown repo"):
        registry.resolve_repo_param("wt")


def test_copied_worktree_git_dir_is_not_the_repo(repo: Path, tmp_path: Path) -> None:
    # A .git directory carrying a commondir, outside the repo's worktrees/.
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "x", str(wt))
    registered = Path((wt / ".git").read_text(encoding="utf-8").split(":", 1)[1].strip())
    copied = tmp_path / "copied"
    shutil.copytree(registered, copied / ".git")
    (copied / ".git" / "commondir").write_text(str(repo / ".git"), encoding="utf-8")
    (copied / ".git" / "gitdir").write_text(str(copied / ".git"), encoding="utf-8")
    assert find_checkout(copied) is None
    assert match_checkout(copied, repo) is None


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("HEAD", "S"),
        ("HEAD~2", "S~2"),
        ("HEAD^", "S^"),
        ("main..HEAD", "main..S"),
        ("origin/main...HEAD", "origin/main...S"),
        ("HEAD..main", "S..main"),
        ("@", "S"),
        ("main..@~1", "main..S~1"),
        ("ORIG_HEAD", "ORIG_HEAD"),
        ("origin/HEAD", "origin/HEAD"),
        ("refs/remotes/origin/HEAD..x", "refs/remotes/origin/HEAD..x"),
        ("abc123", "abc123"),
    ],
)
def test_head_in_a_revspec_means_the_callers_commit(given: str, expected: str) -> None:
    from repowise.server.mcp_server._checkout import _HEAD

    assert _HEAD.sub("S", given) == expected
