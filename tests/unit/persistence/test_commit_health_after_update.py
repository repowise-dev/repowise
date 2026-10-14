"""An update scans commit health after its write transaction, and catches up.

The scan can run for minutes. Inside the persist transaction it held SQLite's
write lock for all of it, blocking every other writer, and it only looked at
the commits the update had just captured, so a commit its budget missed was
never scanned again until a full index.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import repowise.core.analysis.change_health.commit_scan as scan_mod
import repowise.core.pipeline.incremental as incremental_mod
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
from repowise.core.persistence.crud import get_commit_health, upsert_git_commits_bulk
from repowise.core.pipeline.commit_health import (
    DEFAULT_BUDGET_SECONDS,
    UPDATE_BUDGET_SECONDS,
    refresh_commit_health,
    refresh_commit_health_after_update,
)
from repowise.core.pipeline.incremental import persist_incremental_index
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


async def test_update_budget_is_smaller_than_the_full_index(repo, monkeypatch) -> None:
    assert UPDATE_BUDGET_SECONDS < DEFAULT_BUDGET_SECONDS
    engine = create_engine(f"sqlite+aiosqlite:///{repo / '.repowise' / 'wiki.db'}")
    await init_db(engine)
    sf = create_session_factory(engine)
    try:
        async with get_session(sf) as session:
            repo_id = (await insert_repo(session)).id
            await upsert_git_commits_bulk(session, repo_id, _commit_rows())
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
