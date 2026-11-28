"""Full-text rows are per repository in a store several repositories share.

Two repositories with a file at the same path hold the same page id. Each one's
index row has to survive the other's writes and deletes, and a search scoped
to one repository has to answer with that repository's page.
"""

from __future__ import annotations

import pytest
from sqlalchemy.sql import text

from repowise.core.persistence.crud import upsert_page
from repowise.core.persistence.search import AmbiguousPageIdError, FullTextSearch
from tests.unit.persistence.helpers import insert_repo, make_page_kwargs

_PID = "file_page:src/main.py"


async def _two_repos_one_id(session):
    first = await insert_repo(session, name="app", local_path="/tmp/app")
    second = await insert_repo(session, name="clone", local_path="/tmp/clone")
    await upsert_page(
        session, **make_page_kwargs(first.id, content="alpha ledger reconciliation routine")
    )
    await upsert_page(
        session, **make_page_kwargs(second.id, content="bravo telemetry sampling routine")
    )
    await session.commit()
    return first.id, second.id


async def _index(fts: FullTextSearch, repo_id: str, content: str) -> None:
    await fts.index(_PID, "main.py", content, "", "src/main.py", repository_id=repo_id)


async def _rows(engine) -> list[tuple]:
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT repository_id, page_id FROM page_fts"))
        return sorted(rows.fetchall())


async def test_each_repository_keeps_its_own_row(async_engine, async_session):
    first, second = await _two_repos_one_id(async_session)
    fts = FullTextSearch(async_engine)
    await _index(fts, first, "alpha ledger reconciliation routine")
    await _index(fts, second, "bravo telemetry sampling routine")
    # Re-indexing one repository's page replaces only its own row.
    await _index(fts, first, "alpha ledger reconciliation routine, revised")

    assert await _rows(async_engine) == sorted([(first, _PID), (second, _PID)])
    assert await fts.list_indexed_ids(repository_id=first) == {_PID}

    hits = await fts.search("telemetry sampling", repository_id=second)
    assert [(h.repository_id, h.page_id) for h in hits] == [(second, _PID)]
    assert await fts.search("telemetry sampling", repository_id=first) == []

    # Unscoped search answers with both, each naming its repository.
    both = await fts.search("routine")
    assert sorted(h.repository_id for h in both) == sorted([first, second])
    assert {h.target_path for h in both} == {"src/main.py"}


async def test_a_scoped_delete_leaves_the_other_repository_indexed(async_engine, async_session):
    first, second = await _two_repos_one_id(async_session)
    fts = FullTextSearch(async_engine)
    await _index(fts, first, "alpha ledger reconciliation routine")
    await _index(fts, second, "bravo telemetry sampling routine")

    await FullTextSearch(async_engine, repository_id=first).delete_many([_PID])

    assert await _rows(async_engine) == [(second, _PID)]


async def test_an_unscoped_write_of_a_shared_id_refuses_to_guess(async_engine, async_session):
    await _two_repos_one_id(async_session)
    fts = FullTextSearch(async_engine)
    with pytest.raises(AmbiguousPageIdError):
        await fts.index(_PID, "main.py", "some routine prose here", "", "src/main.py")


async def test_an_unscoped_write_in_a_one_repository_store_finds_the_owner(
    async_engine, async_session
):
    repo = await insert_repo(async_session)
    await upsert_page(async_session, **make_page_kwargs(repo.id))
    await async_session.commit()
    fts = FullTextSearch(async_engine)
    await fts.index(_PID, "main.py", "entry point routine prose", "", "src/main.py")
    assert await _rows(async_engine) == [(repo.id, _PID)]


async def test_an_index_not_yet_rebuilt_still_deletes_and_lists(async_engine, async_session):
    """``repowise delete`` and doctor's drift check never rebuild the index, so
    a store indexed before ``page_fts.repository_id`` existed must not make
    their scoped calls fail."""
    repo = await insert_repo(async_session)
    await upsert_page(async_session, **make_page_kwargs(repo.id))
    await async_session.commit()
    async with async_engine.begin() as conn:
        await conn.execute(text("DROP TABLE page_fts"))
        await conn.execute(
            text(
                "CREATE VIRTUAL TABLE page_fts USING fts5(page_id UNINDEXED, title, content, "
                "summary, target_path, vocabulary, digest)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO page_fts VALUES (:p, 'main.py', 'entry point prose', '', "
                "'src/main.py', '', '')"
            ),
            {"p": _PID},
        )
    fts = FullTextSearch(async_engine, repository_id=repo.id)

    assert await fts.list_indexed_ids() == {_PID}
    await fts.delete_many([_PID])
    assert await fts.list_indexed_ids() == set()


async def test_orphan_pruning_matches_rows_by_repository_and_id(async_engine, async_session):
    first, second = await _two_repos_one_id(async_session)
    fts = FullTextSearch(async_engine)
    await _index(fts, first, "alpha ledger reconciliation routine")
    await _index(fts, second, "bravo telemetry sampling routine")
    async with async_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM wiki_pages WHERE repository_id = :r"), {"r": first}
        )

    assert await fts.prune_orphans() == 1
    assert await _rows(async_engine) == [(second, _PID)]
