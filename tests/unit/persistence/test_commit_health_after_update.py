"""An update scans commit health after its write transaction, and catches up.

The scan can run for minutes. Inside the persist transaction it held SQLite's
write lock for all of it, blocking every other writer, and it only looked at
the commits the update had just captured, so a commit its budget missed was
never scanned again until a full index.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import repowise.core.analysis.change_health.commit_scan as scan_mod
import repowise.core.pipeline.commit_health_drain as drain_mod
import repowise.core.pipeline.incremental.persist_index as incremental_mod
from repowise.core.analysis.change_health.commit_scan import (
    CommitHealthScan,
    current_fingerprint,
)
from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
    upsert_repository,
)
from repowise.core.persistence.crud import (
    get_commit_health,
    upsert_commit_health_bulk,
    upsert_git_commits_bulk,
)
from repowise.core.pipeline.commit_health import (
    DEFAULT_BUDGET_SECONDS,
    UPDATE_BUDGET_SECONDS,
    refresh_commit_health,
    refresh_commit_health_after_update,
)
from repowise.core.pipeline.incremental import persist_incremental_index
from repowise.core.update_lock import (
    release_commit_health_lock,
    release_update_lock,
    try_acquire_commit_health_lock,
    try_acquire_update_lock,
)
from tests.unit.persistence.helpers import insert_repo

_BASE = datetime(2026, 1, 1, tzinfo=UTC)
#: Oldest first; committed_at grows with the index.
_SHAS = [f"{i:040x}" for i in range(5)]


def _commit_rows() -> list[dict]:
    return [
        {"sha": sha, "subject": sha[-1], "committed_at": _BASE + timedelta(hours=i)}
        for i, sha in enumerate(_SHAS)
    ]


def _delta_row(sha: str, repo_path: str) -> dict:
    return {"sha": sha, "status": "available", **current_fingerprint(repo_path)}


def _scanner(repo_path: str, per_run: int | None = None, seen: list | None = None):
    """A scan that covers at most *per_run* commits, as a spent budget would."""

    def scan(path, shas, *, budget_seconds=0.0, **_kwargs):
        if seen is not None:
            seen.append((list(shas), budget_seconds))
        done = shas if per_run is None else shas[:per_run]
        return CommitHealthScan(
            delta_rows=[_delta_row(sha, repo_path) for sha in done],
            scanned=len(done),
            exhausted_budget=len(done) < len(shas),
        )

    return scan


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    monkeypatch.delenv("REPOWISE_COMMIT_HEALTH_BUDGET_SECONDS", raising=False)
    (tmp_path / ".repowise").mkdir()
    return tmp_path


def _capture_commits(monkeypatch) -> None:
    """Stand in for the git walk: the update captures these commit rows."""

    async def capture(session, repo_id, repo_path, *, timings=None):
        await upsert_git_commits_bulk(session, repo_id, _commit_rows())

    monkeypatch.setattr(incremental_mod, "persist_incremental_commits", capture)


async def _persist(repo: Path) -> None:
    await persist_incremental_index(
        repo,
        object(),
        {"src/a.py": {"file_path": "src/a.py"}},
        None,
        None,
        ["src/a.py"],
        log=lambda _msg: None,
        degraded=[],
    )


async def _stored_shas(repo: Path) -> set[str]:
    engine = create_engine(f"sqlite+aiosqlite:///{repo / '.repowise' / 'wiki.db'}")
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo_row = await upsert_repository(session, name=repo.name, local_path=str(repo))
            return {
                sha
                for sha in _SHAS
                if await get_commit_health(session, repo_row.id, sha) is not None
            }
    finally:
        await engine.dispose()


async def test_update_scans_with_the_write_lock_free(repo, monkeypatch) -> None:
    """Another writer can take the lock mid-scan, and sees the update's rows."""
    _capture_commits(monkeypatch)
    observed: dict = {}
    inner = _scanner(str(repo))

    def scan(path, shas, **kwargs):
        conn = sqlite3.connect(repo / ".repowise" / "wiki.db", timeout=0)
        try:
            conn.execute("BEGIN IMMEDIATE")  # raises "database is locked" if held
            conn.rollback()
            observed["committed"] = conn.execute("SELECT count(*) FROM git_commits").fetchone()[0]
        finally:
            conn.close()
        return inner(path, shas, **kwargs)

    monkeypatch.setattr(scan_mod, "scan_commits", scan)

    await _persist(repo)

    assert observed["committed"] == len(_SHAS)
    assert await _stored_shas(repo) == set(_SHAS)


async def test_a_failed_scan_leaves_the_persist_committed(repo, monkeypatch) -> None:
    _capture_commits(monkeypatch)

    def boom(*_args, **_kwargs):
        raise RuntimeError("scan blew up")

    monkeypatch.setattr(scan_mod, "scan_commits", boom)

    await _persist(repo)

    conn = sqlite3.connect(repo / ".repowise" / "wiki.db")
    try:
        assert conn.execute("SELECT count(*) FROM git_commits").fetchone()[0] == len(_SHAS)
    finally:
        conn.close()
    assert await _stored_shas(repo) == set()


async def test_an_update_with_no_git_changes_still_drains_the_backlog(
    repo, monkeypatch
) -> None:
    """Empty git maps capture nothing new, but earlier skipped commits get scanned."""
    _capture_commits(monkeypatch)
    monkeypatch.setattr(scan_mod, "scan_commits", _scanner(str(repo), 0))
    await _persist(repo)
    assert await _stored_shas(repo) == set()

    monkeypatch.setattr(scan_mod, "scan_commits", _scanner(str(repo)))
    await persist_incremental_index(
        repo, object(), {}, None, None, [], log=lambda _msg: None, degraded=[]
    )

    assert await _stored_shas(repo) == set(_SHAS)


async def test_commits_a_budget_skipped_are_scanned_by_the_next_update(
    repo, monkeypatch
) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{repo / '.repowise' / 'wiki.db'}")
    await init_db(engine)
    sf = create_session_factory(engine)
    try:
        async with get_session(sf) as session:
            repo_id = (await insert_repo(session)).id
            await upsert_git_commits_bulk(session, repo_id, _commit_rows())
        seen: list = []
        monkeypatch.setattr(scan_mod, "scan_commits", _scanner(str(repo), 2, seen))

        first = await refresh_commit_health_after_update(sf, repo_id, str(repo))
        second = await refresh_commit_health_after_update(sf, repo_id, str(repo))
        third = await refresh_commit_health_after_update(sf, repo_id, str(repo))

        newest_first = list(reversed(_SHAS))
        assert seen[0][0] == newest_first
        assert seen[1][0] == newest_first[2:]
        assert seen[2][0] == newest_first[4:]
        assert [first["stored"], second["stored"], third["stored"]] == [2, 2, 1]
        # Nothing left: a fourth update does not scan at all.
        assert (await refresh_commit_health_after_update(sf, repo_id, str(repo)))[
            "candidates"
        ] == 0
        assert len(seen) == 3
    finally:
        await engine.dispose()


async def _store_with_commits(repo: Path):
    engine = create_engine(f"sqlite+aiosqlite:///{repo / '.repowise' / 'wiki.db'}")
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        repo_id = (await insert_repo(session)).id
        await upsert_git_commits_bulk(session, repo_id, _commit_rows())
    return engine, sf, repo_id


async def _stored_in(sf, repo_id: str) -> set[str]:
    async with get_session(sf) as session:
        return {sha for sha in _SHAS if await get_commit_health(session, repo_id, sha) is not None}


async def test_update_budget_is_smaller_than_the_full_index(repo, monkeypatch) -> None:
    assert UPDATE_BUDGET_SECONDS < DEFAULT_BUDGET_SECONDS
    engine, sf, repo_id = await _store_with_commits(repo)
    try:
        seen: list = []
        monkeypatch.setattr(scan_mod, "scan_commits", _scanner(str(repo), 0, seen))

        await refresh_commit_health_after_update(sf, repo_id, str(repo))
        async with get_session(sf) as session:
            await refresh_commit_health(session, repo_id, str(repo), list(_SHAS))
        monkeypatch.setenv("REPOWISE_COMMIT_HEALTH_BUDGET_SECONDS", "7")
        await refresh_commit_health_after_update(sf, repo_id, str(repo))

        assert [budget for _shas, budget in seen] == [
            UPDATE_BUDGET_SECONDS,
            DEFAULT_BUDGET_SECONDS,
            7.0,
        ]
    finally:
        await engine.dispose()


async def test_an_older_analyzers_backlog_keeps_the_inline_budget(repo, monkeypatch) -> None:
    """A re-pended backlog no longer buys minutes inline; the background drain takes it."""
    engine, sf, repo_id = await _store_with_commits(repo)
    try:
        async with get_session(sf) as session:
            stale = {**_delta_row(_SHAS[0], str(repo)), "analyzer_version": 0}
            await upsert_commit_health_bulk(session, repo_id, [stale], [])
        seen: list = []
        monkeypatch.setattr(scan_mod, "scan_commits", _scanner(str(repo), 0, seen))

        await refresh_commit_health_after_update(sf, repo_id, str(repo))

        assert [budget for _shas, budget in seen] == [UPDATE_BUDGET_SECONDS]
    finally:
        await engine.dispose()


@pytest.fixture
def spawned(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(
        drain_mod, "start_background_drain", lambda path, repo_id: calls.append((path, repo_id))
    )
    return calls


@pytest.mark.parametrize(
    ("per_run", "background", "expected"), [(2, True, 1), (None, True, 0), (2, False, 0)]
)
async def test_only_a_spent_budget_hands_off_to_the_background(
    repo, monkeypatch, spawned, per_run, background, expected
) -> None:
    engine, sf, repo_id = await _store_with_commits(repo)
    try:
        monkeypatch.setattr(scan_mod, "scan_commits", _scanner(str(repo), per_run))

        stats = await refresh_commit_health_after_update(
            sf, repo_id, str(repo), background=background
        )

        assert stats["exhausted"] == int(per_run is not None)
        assert spawned == [(str(repo), repo_id)] * expected
    finally:
        await engine.dispose()


async def test_a_held_lock_skips_the_inline_scan_and_queues_a_drain(
    repo, monkeypatch, spawned
) -> None:
    """One scanner per repo: the update defers to a running drain instead of racing it."""
    engine, sf, repo_id = await _store_with_commits(repo)
    seen: list = []
    monkeypatch.setattr(scan_mod, "scan_commits", _scanner(str(repo), None, seen))
    assert try_acquire_commit_health_lock(repo) is None
    try:
        stats = await refresh_commit_health_after_update(sf, repo_id, str(repo), background=True)
        assert stats["deferred"] == 1
        assert seen == []
        assert spawned == [(str(repo), repo_id)]
        assert drain_mod.wait_for_turn(repo, timeout=0) is False
    finally:
        release_commit_health_lock(repo)
        await engine.dispose()
    assert drain_mod.wait_for_turn(repo, timeout=0) is True
    release_commit_health_lock(repo)


async def test_the_inline_scan_releases_its_lock(repo, monkeypatch) -> None:
    engine, sf, repo_id = await _store_with_commits(repo)
    try:
        monkeypatch.setattr(scan_mod, "scan_commits", _scanner(str(repo), 2))
        await refresh_commit_health_after_update(sf, repo_id, str(repo))
        assert try_acquire_commit_health_lock(repo) is None
        release_commit_health_lock(repo)
    finally:
        await engine.dispose()


async def test_a_running_update_holds_the_drain_back(repo) -> None:
    assert try_acquire_update_lock(repo, None) is None
    try:
        assert drain_mod.wait_for_turn(repo, timeout=0) is False
    finally:
        release_update_lock(repo)


async def test_the_drain_fills_what_the_inline_budget_left(repo, monkeypatch, spawned) -> None:
    engine, sf, repo_id = await _store_with_commits(repo)
    try:
        seen: list = []
        monkeypatch.setattr(scan_mod, "scan_commits", _scanner(str(repo), 2, seen))
        await refresh_commit_health_after_update(sf, repo_id, str(repo), background=True)
        assert await _stored_in(sf, repo_id) == set(_SHAS[-2:])
        assert spawned

        stored = await drain_mod.drain(sf, repo_id, repo, total=60, round_seconds=5)

        assert stored == 3
        assert await _stored_in(sf, repo_id) == set(_SHAS)
        assert [budget for _shas, budget in seen[1:]] == [5, 5]
    finally:
        await engine.dispose()


async def test_the_drain_stops_between_rounds_when_an_update_starts(repo, monkeypatch) -> None:
    engine, sf, repo_id = await _store_with_commits(repo)
    seen: list = []
    inner = _scanner(str(repo), 1, seen)

    def scan(path, shas, **kwargs):
        try_acquire_update_lock(repo, None)
        return inner(path, shas, **kwargs)

    monkeypatch.setattr(scan_mod, "scan_commits", scan)
    try:
        assert await drain_mod.drain(sf, repo_id, repo, total=60) == 1
        assert len(seen) == 1
    finally:
        release_update_lock(repo)
        await engine.dispose()


async def test_the_drain_process_entry_scans_the_repo_store_and_frees_the_lock(
    repo, monkeypatch
) -> None:
    engine, sf, repo_id = await _store_with_commits(repo)
    try:
        monkeypatch.setattr(scan_mod, "scan_commits", _scanner(str(repo), 2))

        assert await asyncio.to_thread(drain_mod.main, [str(repo), repo_id]) == 0

        assert await _stored_in(sf, repo_id) == set(_SHAS)
        assert try_acquire_commit_health_lock(repo) is None
        release_commit_health_lock(repo)
    finally:
        await engine.dispose()


def test_the_drain_is_spawned_isolated_from_the_repo_cwd(repo, monkeypatch) -> None:
    import repowise.core.spawn as spawn_mod

    calls: list = []
    monkeypatch.setattr(spawn_mod, "spawn_detached", lambda argv, cwd: calls.append((argv, cwd)))
    monkeypatch.setenv(drain_mod.BACKGROUND_ENV, "1")
    drain_mod.start_background_drain(str(repo), "rid")
    monkeypatch.setenv(drain_mod.BACKGROUND_ENV, "0")
    drain_mod.start_background_drain(str(repo), "rid")

    assert len(calls) == 1
    argv, cwd = calls[0]
    assert argv[1:] == ["-P", "-m", drain_mod.__name__, str(repo), "rid"]
    assert cwd == str(repo)
