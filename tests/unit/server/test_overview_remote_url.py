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

import pytest

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


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("https://user:glpat-secret@gitlab.com/g/sub/p.git", "https://gitlab.com/g/sub/p.git"),
        ("https://ghp_secret@github.com/o/r.git", "https://github.com/o/r.git"),
        (
            "https://user%40corp.com:pat@dev.azure.com/org/proj/_git/repo",
            "https://dev.azure.com/org/proj/_git/repo",
        ),
        ("http://u:p@git.corp:8080/g/p", "http://git.corp:8080/g/p"),
        ("ssh://git:secret@git.corp:2222/g/p.git", "ssh://git@git.corp:2222/g/p.git"),
        ("ssh://git@github.com/o/r.git", "ssh://git@github.com/o/r.git"),
        ("git@github.com:o/r.git", "git@github.com:o/r.git"),
        ("https://github.com/o/r", "https://github.com/o/r"),
        ("HTTPS://tok@github.com/o/r", "HTTPS://github.com/o/r"),
        ("  https://ghp_tok@github.com/o/r  ", "https://github.com/o/r"),
        ("git+https://ghp_tok@github.com/o/r", "git+https://github.com/o/r"),
        ("ftp://tok@host/x", "ftp://host/x"),
        ("https://user:pa/ss@host/p.git", "https://host/p.git"),
        ("https://gitlab.com/g/p.git?private_token=abc#frag", "https://gitlab.com/g/p.git"),
        ("https://gitlab.com/g/@weird/p", "https://gitlab.com/g/@weird/p"),
        ("ssh://ghp_tok@host/x", "ssh://ghp_tok@host/x"),
        ("user:pw@host:g/p.git", "user@host:g/p.git"),
        ("https://[::1]:8443/g/p", "https://[::1]:8443/g/p"),
        ("file:///srv/git/p.git", "file:///srv/git/p.git"),
    ],
)
def test_credentials_never_reach_the_ui(tmp_path: Path, remote: str, expected: str) -> None:
    """The remote is sent to the browser, so a token in userinfo must not survive."""
    assert _remote_url(remote, None) == expected
    _write_config(tmp_path / ".git", f'[remote "origin"]\n\turl = {remote}\n')
    assert _remote_url("", str(tmp_path)) == expected

