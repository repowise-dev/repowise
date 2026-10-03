"""Unit tests for git-worktree detection and seed preconditions."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repowise.cli.worktree import base_is_seedable, detect_worktree_base


def _git(args: list[str], cwd: Path) -> None:
    subprocess.check_call(
        ["git", *args],
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.fixture()
def base_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "base"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.email", "t@t.t"], repo)
    _git(["config", "user.name", "t"], repo)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "init"], repo)
    return repo


def test_plain_repo_is_not_a_worktree(base_repo: Path) -> None:
    assert detect_worktree_base(base_repo) is None


def test_non_git_dir_is_not_a_worktree(tmp_path: Path) -> None:
    d = tmp_path / "plain"
    d.mkdir()
    assert detect_worktree_base(d) is None


def test_linked_worktree_resolves_base(base_repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    _git(["worktree", "add", "-b", "feature", str(wt)], base_repo)
    detected = detect_worktree_base(wt)
    assert detected is not None
    assert detected.resolve() == base_repo.resolve()


def test_submodule_git_file_is_not_a_worktree(base_repo: Path, tmp_path: Path) -> None:
    # Fake a submodule layout: .git file whose gitdir points at the parent's
    # modules dir, which does not end in a bare ".git" component.
    sub = tmp_path / "sub"
    sub.mkdir()
    modules = base_repo / ".git" / "modules" / "sub"
    modules.mkdir(parents=True)
    (sub / ".git").write_text(f"gitdir: {modules}\n", encoding="utf-8")
    assert detect_worktree_base(sub) is None


def test_base_is_seedable_requires_state_and_db(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    (repo / ".repowise").mkdir(parents=True)
    assert not base_is_seedable(repo)
    (repo / ".repowise" / "state.json").write_text("{}", encoding="utf-8")
    assert not base_is_seedable(repo)
    (repo / ".repowise" / "wiki.db").write_text("", encoding="utf-8")
    assert base_is_seedable(repo)


def test_adopting_identity_rekeys_the_repo_level_pages(tmp_path: Path) -> None:
    """The overview id carries the repo name; the seeded copy must be findable and regenerable."""
    import sqlite3
    from contextlib import closing

    from repowise.cli.worktree import _adopt_repository_identity

    src, dest = tmp_path / "base", tmp_path / "feature-tree"
    db = tmp_path / "wiki.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript(
            "CREATE TABLE repositories (id TEXT, name TEXT, local_path TEXT);"
            "CREATE TABLE wiki_pages (id TEXT, repository_id TEXT, page_type TEXT,"
            " target_path TEXT, parent_page_id TEXT);"
            "CREATE TABLE wiki_page_versions (id TEXT, page_id TEXT);"
            "CREATE VIRTUAL TABLE page_fts USING fts5(page_id UNINDEXED, title, target_path);"
        )
        conn.execute("INSERT INTO repositories VALUES ('r1', 'base', ?)", (str(src),))
        conn.execute(
            "INSERT INTO wiki_pages VALUES ('repo_overview:base', 'r1', 'repo_overview', 'base', NULL)"
        )
        conn.execute(
            "INSERT INTO wiki_pages VALUES ('module_page:src', 'r1', 'module_page', 'src',"
            " 'repo_overview:base')"
        )
        conn.execute("INSERT INTO wiki_page_versions VALUES ('v1', 'repo_overview:base')")
        conn.execute("INSERT INTO page_fts VALUES ('repo_overview:base', 'Overview', 'base')")
        conn.commit()

    _adopt_repository_identity(tmp_path, src_repo=src, dest_repo=dest)

    with closing(sqlite3.connect(db)) as conn:
        pages = conn.execute("SELECT id, target_path, parent_page_id FROM wiki_pages").fetchall()
        versions = conn.execute("SELECT page_id FROM wiki_page_versions").fetchall()
        fts = conn.execute("SELECT page_id, target_path FROM page_fts").fetchall()
    assert ("repo_overview:feature-tree", "feature-tree", None) in pages
    assert ("module_page:src", "src", "repo_overview:feature-tree") in pages
    assert versions == [("repo_overview:feature-tree",)]
    assert fts == [("repo_overview:feature-tree", "feature-tree")]
