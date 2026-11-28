"""Page identity is per repository in a database several repositories share.

A page id is ``f"{page_type}:{target_path}"``, which says nothing about the
repository. Two repositories indexed into one database (``REPOWISE_DB_URL``)
with a file at the same relative path produce the same page id, and each must
keep its own page: the second repository's write may neither overwrite the
first repository's row nor be dropped in its favour.
"""

from __future__ import annotations

from sqlalchemy import func, select

from repowise.core.persistence.crud import list_page_ids, upsert_page
from repowise.core.persistence.models import Page
from tests.unit.persistence.helpers import insert_repo, make_page_kwargs


async def _two_repos(session):
    first = await insert_repo(session, name="app", local_path="/tmp/app")
    second = await insert_repo(session, name="app-clone", local_path="/tmp/app-clone")
    return first, second


async def test_same_page_id_in_two_repos_keeps_both_pages(async_session):
    first, second = await _two_repos(async_session)

    await upsert_page(async_session, **make_page_kwargs(first.id, content="first repo"))
    await upsert_page(async_session, **make_page_kwargs(second.id, content="second repo"))
    await async_session.commit()

    assert await list_page_ids(async_session, first.id) == ["file_page:src/main.py"]
    assert await list_page_ids(async_session, second.id) == ["file_page:src/main.py"]

    rows = (
        await async_session.execute(
            select(Page.repository_id, Page.content, Page.version).where(
                Page.id == "file_page:src/main.py"
            )
        )
    ).all()
    assert sorted(rows) == sorted(
        [(first.id, "first repo", 1), (second.id, "second repo", 1)]
    )


async def test_second_repo_write_does_not_version_first_repos_page(async_session):
    first, second = await _two_repos(async_session)

    await upsert_page(async_session, **make_page_kwargs(first.id, content="v1"))
    await upsert_page(async_session, **make_page_kwargs(second.id, content="other"))
    await upsert_page(async_session, **make_page_kwargs(first.id, content="v2"))
    await async_session.commit()

    first_page = (
        await async_session.execute(
            select(Page).where(
                Page.repository_id == first.id, Page.id == "file_page:src/main.py"
            )
        )
    ).scalar_one()
    second_page = (
        await async_session.execute(
            select(Page).where(
                Page.repository_id == second.id, Page.id == "file_page:src/main.py"
            )
        )
    ).scalar_one()
    assert (first_page.content, first_page.version) == ("v2", 2)
    assert (second_page.content, second_page.version) == ("other", 1)
    total = await async_session.scalar(select(func.count()).select_from(Page))
    assert total == 2
