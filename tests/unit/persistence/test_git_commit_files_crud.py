"""CRUD round-trip tests for the git_commit_files table."""

from __future__ import annotations

import pytest

from repowise.core.persistence.crud import (
    delete_git_commit_files,
    delete_git_commit_files_by_sha,
    get_commit_files,
    upsert_git_commit_files_bulk,
)
from tests.unit.persistence.helpers import insert_repo


def _row(sha: str, path: str, added: int = 1, deleted: int = 0) -> dict:
    return {"sha": sha, "file_path": path, "lines_added": added, "lines_deleted": deleted}


@pytest.mark.asyncio
async def test_files_come_back_biggest_churn_first(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_git_commit_files_bulk(
        async_session,
        repo.id,
        [
            _row("aaa", "small.py", 1, 1),
            _row("aaa", "big.py", 40, 20),
            _row("aaa", "mid.py", 5, 5),
        ],
    )
    await async_session.commit()

    files = await get_commit_files(async_session, repo.id, "aaa")

    assert [f.file_path for f in files] == ["big.py", "mid.py", "small.py"]
    assert files[0].lines_added == 40
    assert files[0].lines_deleted == 20


@pytest.mark.asyncio
async def test_upsert_is_idempotent_on_sha_and_path(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_git_commit_files_bulk(async_session, repo.id, [_row("aaa", "a.py", 1, 1)])
    await async_session.commit()
    await upsert_git_commit_files_bulk(async_session, repo.id, [_row("aaa", "a.py", 9, 3)])
    await async_session.commit()

    files = await get_commit_files(async_session, repo.id, "aaa")

    assert len(files) == 1
    assert (files[0].lines_added, files[0].lines_deleted) == (9, 3)


@pytest.mark.asyncio
async def test_one_path_can_belong_to_many_commits(async_session) -> None:
    """A row is "this commit touched this file", not a file record."""
    repo = await insert_repo(async_session)
    await upsert_git_commit_files_bulk(
        async_session, repo.id, [_row("aaa", "a.py"), _row("bbb", "a.py")]
    )
    await async_session.commit()

    assert len(await get_commit_files(async_session, repo.id, "aaa")) == 1
    assert len(await get_commit_files(async_session, repo.id, "bbb")) == 1


@pytest.mark.asyncio
async def test_rows_leave_with_their_commit(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_git_commit_files_bulk(
        async_session, repo.id, [_row("aaa", "a.py"), _row("bbb", "b.py")]
    )
    await async_session.commit()

    removed = await delete_git_commit_files_by_sha(async_session, repo.id, ["aaa"])
    await async_session.commit()

    assert removed == 1
    assert await get_commit_files(async_session, repo.id, "aaa") == []
    assert len(await get_commit_files(async_session, repo.id, "bbb")) == 1


@pytest.mark.asyncio
async def test_a_clean_reindex_clears_the_table(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_git_commit_files_bulk(async_session, repo.id, [_row("aaa", "a.py")])
    await async_session.commit()

    await delete_git_commit_files(async_session, repo.id)
    await async_session.commit()

    assert await get_commit_files(async_session, repo.id, "aaa") == []


@pytest.mark.asyncio
async def test_a_full_reindex_wipes_the_file_rows_with_their_commits(async_session) -> None:
    """A rewritten history must not leave rows pointing at vanished shas."""
    from repowise.core.pipeline.persist import replace_git_history

    repo = await insert_repo(async_session)
    await upsert_git_commit_files_bulk(async_session, repo.id, [_row("gone", "a.py")])
    await async_session.commit()

    await replace_git_history(async_session, repo.id, {}, None)
    await async_session.commit()

    assert await get_commit_files(async_session, repo.id, "gone") == []
