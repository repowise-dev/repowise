"""Tests for the git remote the Overview header resolves its avatar from.

`repositories.url` is client-supplied and empty for most CLI-registered repos,
so the header would show initials for nearly every local repo if the stored
value were the only source. `_remote_url` falls back to reading `origin` out of
`.git/config`, which is where the answer actually lives.

Every failure path must return None rather than raise: this runs on a page
load, and a malformed git config is not a reason to fail the Overview.
"""

from __future__ import annotations

from pathlib import Path

from repowise.server.routers.overview import _remote_url

_ORIGIN = "https://github.com/repowise-dev/repowise.git"


def _write_config(git_dir: Path, body: str) -> None:
    git_dir.mkdir(parents=True, exist_ok=True)
    (git_dir / "config").write_text(body, encoding="utf-8")


def test_stored_url_wins_without_touching_disk(tmp_path: Path) -> None:
    """An explicitly registered URL is authoritative; no file read happens."""
    assert _remote_url("https://example.com/x/y", str(tmp_path)) == "https://example.com/x/y"


def test_reads_origin_from_git_config(tmp_path: Path) -> None:
    _write_config(tmp_path / ".git", f'[remote "origin"]\n\turl = {_ORIGIN}\n')

    assert _remote_url("", str(tmp_path)) == _ORIGIN


def test_falls_back_to_upstream_when_origin_is_absent(tmp_path: Path) -> None:
    """A fork checkout can carry only `upstream`; that still names the repo."""
    _write_config(tmp_path / ".git", f'[remote "upstream"]\n\turl = {_ORIGIN}\n')

    assert _remote_url(None, str(tmp_path)) == _ORIGIN


def test_follows_a_worktree_gitdir_pointer(tmp_path: Path) -> None:
    """Worktrees keep a `.git` FILE; the config lives in the main checkout.

    Without following the pointer, every worktree — which is how a lot of this
    project's own development happens — would fall back to initials.
    """
    main = tmp_path / "main"
    _write_config(main / ".git", f'[remote "origin"]\n\turl = {_ORIGIN}\n')
    worktree_gitdir = main / ".git" / "worktrees" / "feature"
    worktree_gitdir.mkdir(parents=True)

    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text(f"gitdir: {worktree_gitdir}\n", encoding="utf-8")

    assert _remote_url("", str(linked)) == _ORIGIN


def test_returns_none_without_a_git_dir(tmp_path: Path) -> None:
    assert _remote_url("", str(tmp_path)) is None


def test_returns_none_for_a_malformed_config(tmp_path: Path) -> None:
    """A config we cannot parse degrades to initials, not to a 500."""
    _write_config(tmp_path / ".git", "this is not ini\n=== nope ===\n")

    assert _remote_url("", str(tmp_path)) is None


def test_returns_none_when_no_remote_is_configured(tmp_path: Path) -> None:
    """A repo with local branches and no remote is a normal repo, not an error."""
    _write_config(tmp_path / ".git", '[core]\n\tbare = false\n[branch "main"]\n')

    assert _remote_url("", str(tmp_path)) is None


def test_returns_none_without_a_local_path() -> None:
    assert _remote_url("", None) is None
