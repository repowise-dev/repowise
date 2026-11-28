from __future__ import annotations

import os
from pathlib import Path

import git as gitpython
import pytest
from sqlalchemy import select

from repowise.core.persistence.crud import upsert_repository
from repowise.core.persistence.models import GitCommit
from repowise.core.pipeline.incremental.commits import persist_incremental_commits


def _configure_author(repo, name: str, email: str) -> None:
    with repo.config_writer() as cw:
        cw.set_value("user", "name", name)
        cw.set_value("user", "email", email)


@pytest.mark.asyncio
async def test_persist_incremental_commits_retains_same_second_commit(
    tmp_path: Path, async_session
) -> None:
    repo = gitpython.Repo.init(tmp_path)
    _configure_author(repo, "Test Author", "author@example.com")
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = "1700000000 +0000"
    env["GIT_COMMITTER_DATE"] = "1700000000 +0000"
    env["GIT_AUTHOR_NAME"] = "Test Author"
    env["GIT_AUTHOR_EMAIL"] = "author@example.com"
    env["GIT_COMMITTER_NAME"] = "Test Author"
    env["GIT_COMMITTER_EMAIL"] = "author@example.com"

    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    repo.git.add("a.py")
    repo.git.commit("-m", "commit 1", env=env)
    c1_sha = repo.head.commit.hexsha

    repo_id = "test-repo"
    await upsert_repository(
        async_session, repo_id=repo_id, name="test-repo", local_path=str(tmp_path)
    )
    # First persist: stores commit 1
    await persist_incremental_commits(async_session, repo_id, tmp_path)
    await async_session.commit()

    # Second commit in the exact same second
    f.write_text("x = 2\n")
    repo.git.add("a.py")
    repo.git.commit("-m", "commit 2", env=env)
    c2_sha = repo.head.commit.hexsha

    # Second persist: must store commit 2 even though it shares timestamp with commit 1
    await persist_incremental_commits(async_session, repo_id, tmp_path)
    await async_session.commit()

    rows = (
        (await async_session.execute(select(GitCommit).where(GitCommit.repository_id == repo_id)))
        .scalars()
        .all()
    )
    stored_shas = {r.sha for r in rows}
    assert c1_sha in stored_shas
    assert c2_sha in stored_shas
