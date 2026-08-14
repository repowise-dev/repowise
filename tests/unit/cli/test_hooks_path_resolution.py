"""Tests for resolving the hooks directory git will actually run.

The installer used to hardcode ``<root>/.git/hooks``, which is wrong in three
layouts that are all common in practice:

* a linked worktree, where ``.git`` is a *file* and ``mkdir`` raised
  ``NotADirectoryError``, killing the command;
* a repo with ``core.hooksPath`` set (husky, lefthook), where git ignores
  ``.git/hooks`` entirely, so the hook installed "successfully" and then never
  ran;
* husky specifically, whose ``core.hooksPath`` points at a generated, gitignored
  ``.husky/_`` that is recreated on every ``npm install``.

Resolution now defers to ``git rev-parse --git-path hooks``.
"""

from __future__ import annotations

import subprocess

import pytest

from repowise.cli.hooks import (
    _hooks_dir,
    _husky_user_hook_dir,
    husky_pending_reason,
    install,
    status,
    uninstall,
)


def _git(*args: str, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    return tmp_path


@pytest.fixture
def committed_repo(git_repo):
    """A repo with one commit, so ``git worktree add`` is possible."""
    (git_repo / "seed.txt").write_text("seed\n")
    _git("add", "seed.txt", cwd=git_repo)
    _git(
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=T",
        "commit",
        "-qm",
        "seed",
        cwd=git_repo,
    )
    return git_repo


def test_plain_repo_resolves_to_git_hooks(git_repo):
    assert _hooks_dir(git_repo) == git_repo / ".git" / "hooks"


def test_core_hooks_path_is_honoured(git_repo):
    """A hook written to .git/hooks would never run when hooksPath is set."""
    _git("config", "core.hooksPath", "my-hooks", cwd=git_repo)

    assert _hooks_dir(git_repo) == git_repo / "my-hooks"

    assert install(git_repo) == "installed"
    assert (git_repo / "my-hooks" / "post-commit").exists()
    assert not (git_repo / ".git" / "hooks" / "post-commit").exists()


def test_absolute_core_hooks_path_is_honoured(git_repo, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    _git("config", "core.hooksPath", str(outside), cwd=git_repo)

    assert _hooks_dir(git_repo) == outside


def test_husky_generated_dir_remaps_to_user_hook_dir(git_repo):
    """`.husky/_` is regenerated and gitignored; the durable path is `.husky/`."""
    husky_generated = git_repo / ".husky" / "_"
    husky_generated.mkdir(parents=True)
    (husky_generated / "h").write_text("#!/usr/bin/env sh\n")
    (husky_generated / ".gitignore").write_text("*\n")
    _git("config", "core.hooksPath", ".husky/_", cwd=git_repo)

    assert _hooks_dir(git_repo) == git_repo / ".husky"

    assert install(git_repo) == "installed"
    assert (git_repo / ".husky" / "post-commit").exists()
    # Not in the generated dir, which the next `npm install` would wipe.
    assert not (husky_generated / "post-commit").exists()


def test_husky_remap_without_generated_helpers(git_repo):
    """A fresh worktree has committed `.husky/` but no generated `_` yet."""
    _git("config", "core.hooksPath", ".husky/_", cwd=git_repo)
    (git_repo / ".husky").mkdir()

    assert _hooks_dir(git_repo) == git_repo / ".husky"


def test_directory_merely_named_underscore_is_not_remapped(tmp_path):
    """Only husky's layout is special-cased, not any directory called `_`."""
    plain = tmp_path / "hooks" / "_"
    plain.mkdir(parents=True)

    assert _husky_user_hook_dir(plain) == plain


def test_install_in_linked_worktree_does_not_raise(committed_repo, tmp_path):
    """Regression: `.git` is a file in a worktree, so mkdir raised NotADirectoryError."""
    worktree = tmp_path / "wt"
    _git("worktree", "add", "-q", str(worktree), "-b", "wt-branch", cwd=committed_repo)
    assert (worktree / ".git").is_file()

    assert install(worktree) == "installed"
    assert status(worktree) == "installed"


def test_install_status_uninstall_agree_on_location(git_repo):
    """All three entry points must resolve the same directory."""
    _git("config", "core.hooksPath", ".husky/_", cwd=git_repo)
    # A fully set-up husky, so the status strings carry no pending-reason suffix.
    generated = git_repo / ".husky" / "_"
    generated.mkdir(parents=True)
    (generated / "h").write_text("#!/usr/bin/env sh\n")

    assert install(git_repo) == "installed"
    assert status(git_repo) == "installed"
    assert uninstall(git_repo) == "removed"
    assert status(git_repo) == "not installed"


def test_husky_dispatches_without_a_preexisting_user_hook(git_repo):
    """A missing sibling user hook does not stop dispatch.

    husky writes a shim for all 14 hook names on every install regardless of what
    is in ``.husky/``, so ``post-commit`` is dispatched immediately -- there is no
    need to wait for the next ``npm install``.
    """
    generated = git_repo / ".husky" / "_"
    generated.mkdir(parents=True)
    (generated / "h").write_text("#!/usr/bin/env sh\n")
    (generated / "post-commit").write_text('#!/usr/bin/env sh\n. "$(dirname "$0")/h"\n')
    _git("config", "core.hooksPath", ".husky/_", cwd=git_repo)
    # Only pre-commit exists as a user hook; post-commit does not.
    (git_repo / ".husky" / "pre-commit").write_text("npm test\n")

    assert install(git_repo) == "installed"
    assert husky_pending_reason(git_repo / ".husky") is None
    assert status(git_repo) == "installed"


def test_husky_not_installed_is_reported_not_silent(git_repo):
    """Without husky's generated dir, core.hooksPath points nowhere and git runs nothing.

    The hook still belongs in ``.husky/`` because that survives, but claiming a
    bare "installed" would repeat the very failure this resolver exists to fix.
    """
    _git("config", "core.hooksPath", ".husky/_", cwd=git_repo)
    (git_repo / ".husky").mkdir()  # committed dir, but husky never ran

    reason = husky_pending_reason(git_repo / ".husky")
    assert reason is not None
    assert "husky is not set up" in reason

    result = install(git_repo)
    assert result.startswith("installed (")
    assert "no .husky/_" in result
    assert (git_repo / ".husky" / "post-commit").exists()
    assert "husky is not set up" in status(git_repo)


def test_pending_reason_is_silent_for_non_husky_layouts(git_repo):
    assert husky_pending_reason(git_repo / ".git" / "hooks") is None
    assert husky_pending_reason(git_repo / "my-hooks") is None


def test_falls_back_when_git_unavailable(git_repo, monkeypatch):
    """Without a usable git binary, keep the historical guess rather than fail."""

    def _boom(*_args, **_kwargs):
        raise OSError("no git")

    monkeypatch.setattr(subprocess, "run", _boom)

    assert _hooks_dir(git_repo) == git_repo / ".git" / "hooks"
