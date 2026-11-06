"""The repository row's sync time moves only when the index syncs.

Every "indexed at" surface reads ``crud.index_synced_at``, so a write that
merely opens the row (a decision command, a settings edit, server
registration) must leave it alone, or a days-old index reads as fresh.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from repowise.core.persistence import crud

_SYNCED = datetime(2026, 10, 18, 9, 30, tzinfo=UTC)


async def _synced_repo(session):
    return await _synced_repo_at(session, "/tmp/r")


def test_accessor_reads_naive_as_utc_and_tolerates_missing() -> None:
    naive = datetime(2026, 10, 18, 9, 30)
    assert crud.index_synced_at(SimpleNamespace(updated_at=naive)) == _SYNCED
    assert crud.index_synced_at(SimpleNamespace(updated_at=_SYNCED)) == _SYNCED
    assert crud.index_synced_at(None) is None
    assert crud.index_synced_at(SimpleNamespace(updated_at=None)) is None


async def test_non_sync_writes_leave_the_stamp(async_session) -> None:
    repo = await _synced_repo(async_session)

    # Opening the row the way decisions and server registration do, with a
    # changed name and settings, then the stage stamp and a settings edit.
    await crud.upsert_repository(
        async_session, name="renamed", local_path="/tmp/r", settings={"k": 1}
    )
    await crud.stamp_analysis_ran(async_session, repo.id, "dead_code")
    repo.settings_json = '{"edited": true}'
    await async_session.flush()
    await async_session.refresh(repo)

    assert repo.name == "renamed"
    assert crud.index_synced_at(repo) == _SYNCED


async def test_a_sync_moves_the_stamp(async_session) -> None:
    repo = await _synced_repo(async_session)
    before = datetime.now(UTC) - timedelta(seconds=5)

    await crud.upsert_repository(async_session, name="r", local_path="/tmp/r", head_commit="b" * 40)
    await async_session.refresh(repo)

    assert crud.index_synced_at(repo) >= before


async def test_up_to_date_restamp_moves_the_stamp(tmp_path, monkeypatch) -> None:
    """The "already up to date" path syncs too, so it advances the stamp."""
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.database import init_db
    from repowise.core.workspace.update import reconcile_repo_head_commit

    url = f"sqlite+aiosqlite:///{(tmp_path / 'wiki.db').as_posix()}"
    monkeypatch.setenv("REPOWISE_DB_URL", url)
    engine = create_engine(url)
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        await _synced_repo_at(session, str(tmp_path))
    before = datetime.now(UTC) - timedelta(seconds=5)

    await reconcile_repo_head_commit(tmp_path, "a" * 40)

    async with get_session(sf) as session:
        repo = await crud.get_repository_by_path(session, str(tmp_path))
        assert crud.index_synced_at(repo) >= before
    await engine.dispose()


async def _synced_repo_at(session, local_path: str):
    repo = await crud.upsert_repository(
        session, name="r", local_path=local_path, head_commit="a" * 40
    )
    repo.updated_at = _SYNCED
    await session.flush()
    return repo
