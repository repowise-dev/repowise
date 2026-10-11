"""Repository deletion under real foreign-key enforcement.

The shared ``async_engine`` fixture builds its engine with
``create_async_engine`` directly, which never issues ``PRAGMA foreign_keys=ON``
— SQLite's default is *off*, so foreign keys are not enforced anywhere in the
persistence suite. Production engines come from
:func:`repowise.core.persistence.database.create_engine`, which turns the
pragma on for every connection. A referential bug is therefore invisible to
the existing tests and fails only against a real store, which is how
``delete_repository`` shipped unable to delete any repository whose pages had
ever been regenerated.

These tests use the project's own ``create_engine`` so the pragmas match what
a user's ``.repowise/wiki.db`` actually runs with.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repowise.core.persistence.crud import (
    delete_repository,
    get_page_versions,
    get_repository,
    upsert_page,
)
from repowise.core.persistence.database import create_engine, init_db
from repowise.core.persistence.models import Page, PageVersion
from tests.unit.persistence.helpers import insert_repo, make_page_kwargs


@pytest.fixture
async def fk_session():
    """Session on an in-memory store with production SQLite pragmas."""
    engine = create_engine("sqlite+aiosqlite:///:memory:", use_static_pool=True)
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def test_foreign_keys_are_enforced(fk_session):
    """Guard the guard: without this pragma the tests below prove nothing."""
    enabled = (await fk_session.execute(text("PRAGMA foreign_keys"))).scalar()
    assert enabled == 1


async def test_delete_repository_with_archived_page_versions(fk_session):
    """A regenerated page leaves a snapshot; deleting the repo must still work.

    ``PageVersion.page_id`` is declared without ``ondelete="CASCADE"``, so the
    cascade that removes the repository's ``Page`` rows used to strand the
    snapshots and abort with ``sqlite3.IntegrityError: FOREIGN KEY constraint
    failed``, surfacing as HTTP 500 from ``DELETE /api/repos/{id}``.
    """
    repo = await insert_repo(fk_session, name="doomed", local_path="/tmp/doomed")
    kwargs = make_page_kwargs(repo.id)

    page = await upsert_page(fk_session, **kwargs)
    # Second upsert archives the first revision as a PageVersion.
    await upsert_page(fk_session, **{**kwargs, "content": "# Rewritten\n\nSecond pass."})
    await fk_session.commit()

    assert await get_page_versions(fk_session, page.id), "expected an archived snapshot"

    deleted = await delete_repository(fk_session, repo.id)
    await fk_session.commit()

    assert deleted is True
    assert await get_repository(fk_session, repo.id) is None
    assert (await fk_session.execute(select(func.count()).select_from(PageVersion))).scalar() == 0
    assert (await fk_session.execute(select(func.count()).select_from(Page))).scalar() == 0


async def test_delete_repository_leaves_other_repos_untouched(fk_session):
    """The snapshot sweep is scoped by repository_id, not global."""
    doomed = await insert_repo(fk_session, name="doomed", local_path="/tmp/doomed")
    keeper = await insert_repo(fk_session, name="keeper", local_path="/tmp/keeper")

    for repo in (doomed, keeper):
        kwargs = make_page_kwargs(repo.id, page_id=f"file_page:{repo.name}/main.py")
        await upsert_page(fk_session, **kwargs)
        await upsert_page(fk_session, **{**kwargs, "content": "# Rewritten"})
    await fk_session.commit()

    assert await delete_repository(fk_session, doomed.id) is True
    await fk_session.commit()

    surviving = (
        (
            await fk_session.execute(
                select(PageVersion).where(PageVersion.repository_id == keeper.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(surviving) == 1
    assert await get_repository(fk_session, keeper.id) is not None
