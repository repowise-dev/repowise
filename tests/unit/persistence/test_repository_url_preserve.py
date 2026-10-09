"""Regression tests for repository URL preservation (issue #3092)."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.persistence.crud import upsert_repository


def _write_origin(repo_dir: Path, url: str) -> None:
    git_dir = repo_dir / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "config").write_text(
        "[remote \"origin\"]\n\turl = " + url + "\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_reregister_without_url_preserves_existing_url(async_session, tmp_path: Path) -> None:
    checkout = tmp_path / "example"
    checkout.mkdir()
    stored = "https://git.example.test/team/example-repo.git"
    created = await upsert_repository(async_session, name="example", local_path=str(checkout), url=stored)
    await async_session.flush()
    updated = await upsert_repository(async_session, name="example", local_path=str(checkout))
    assert updated.id == created.id
    assert updated.url == stored


@pytest.mark.asyncio
async def test_explicit_url_replaces_and_empty_string_clears(async_session, tmp_path: Path) -> None:
    checkout = tmp_path / "example"
    checkout.mkdir()
    await upsert_repository(async_session, name="example", local_path=str(checkout), url="https://git.example.test/old.git")
    replaced = await upsert_repository(async_session, name="example", local_path=str(checkout), url="https://git.example.test/new.git")
    assert replaced.url == "https://git.example.test/new.git"
    cleared = await upsert_repository(async_session, name="example", local_path=str(checkout), url="")
    assert cleared.url == ""


@pytest.mark.asyncio
async def test_create_without_url_reads_origin_and_skips_missing_remote(async_session, tmp_path: Path) -> None:
    with_origin = tmp_path / "with-origin"
    with_origin.mkdir()
    origin = "https://git.example.test/team/with-origin.git"
    _write_origin(with_origin, origin)
    created = await upsert_repository(async_session, name="with-origin", local_path=str(with_origin))
    assert created.url == origin
    bare = tmp_path / "no-remote"
    bare.mkdir()
    (bare / ".git").mkdir()
    empty = await upsert_repository(async_session, name="no-remote", local_path=str(bare))
    assert empty.url == ""


@pytest.mark.asyncio
async def test_create_follows_worktree_gitdir_to_origin(async_session, tmp_path: Path) -> None:
    common = tmp_path / "common"
    common.mkdir()
    origin = "git@git.example.test:team/worktree.git"
    (common / "config").write_text("[remote \"origin\"]\n\turl = " + origin + "\n", encoding="utf-8")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: " + str(common) + "\n", encoding="utf-8")
    created = await upsert_repository(async_session, name="worktree", local_path=str(worktree))
    assert created.url == origin
