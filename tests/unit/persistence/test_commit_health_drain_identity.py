"""Moving the scan to the background changes when rows land, never what they are."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import git as gitpython
import pytest

import repowise.core.analysis.change_health.commit_scan as scan_mod
import repowise.core.pipeline.commit_health_drain as drain_mod
from repowise.core.persistence import create_engine, create_session_factory, get_session, init_db
from repowise.core.persistence.crud import upsert_git_commits_bulk
from repowise.core.pipeline.commit_health import (
    refresh_commit_health,
    refresh_commit_health_after_update,
)
from tests.unit.persistence.helpers import insert_repo

_NESTED = """
def tangle(a, b, c, d):
    if a:
        for x in range(b):
            if x % 2:
                while c:
                    if d:
                        try:
                            c -= 1
                        except ValueError:
                            return None
                    else:
                        c -= 2
    return a and b or c and d
"""

_VERSIONS = [
    {"a.py": "def one():\n    return 1\n"},
    {"a.py": "def one():\n    return 1\n" + _NESTED},
    {"b.py": _NESTED.replace("tangle", "knot")},
    {"a.py": "def one():\n    return 1\n"},
]


@pytest.fixture
def history(tmp_path: Path) -> tuple[Path, list[dict]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    r = gitpython.Repo.init(repo, initial_branch="main")
    with r.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    rows = []
    for i, files in enumerate(_VERSIONS):
        for name, text in files.items():
            (repo / name).write_text(text, encoding="utf-8")
        r.index.add(list(files))
        commit = r.index.commit(f"c{i}")
        when = datetime(2026, 1, 1 + i, tzinfo=UTC)
        rows.append({"sha": commit.hexsha, "subject": f"c{i}", "committed_at": when})
    r.close()
    return repo, rows


async def _store(db: Path, rows: list[dict]):
    engine = create_engine(f"sqlite+aiosqlite:///{db}")
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        repo_id = (await insert_repo(session)).id
        await upsert_git_commits_bulk(session, repo_id, rows)
    return engine, sf, repo_id


def _rows(db: Path, table: str) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(f"SELECT * FROM {table}")
        names = [d[0] for d in cur.description]
        keep = [
            i
            for i, n in enumerate(names)
            if n not in ("id", "repository_id") and not n.endswith("_at")
        ]
        return sorted(tuple(row[i] for i in keep) for row in cur.fetchall())
    finally:
        conn.close()


async def test_inline_plus_drain_stores_the_same_rows_as_one_full_scan(
    history, tmp_path, monkeypatch
) -> None:
    repo, rows = history
    newest_first = [row["sha"] for row in reversed(rows)]
    full_db, split_db = tmp_path / "full.db", tmp_path / "split.db"

    engine, sf, repo_id = await _store(full_db, rows)
    try:
        async with get_session(sf) as session:
            await refresh_commit_health(session, repo_id, str(repo), newest_first)
    finally:
        await engine.dispose()

    real = scan_mod.scan_commits

    def one_per_call(path, shas, **kwargs):
        # Stands in for a budget that covers one commit per call.
        scan = real(path, shas[:1], **kwargs)
        scan.exhausted_budget = len(shas) > 1
        return scan

    monkeypatch.setattr(scan_mod, "scan_commits", one_per_call)
    monkeypatch.setattr(drain_mod, "start_background_drain", lambda *_args: None)
    engine, sf, repo_id = await _store(split_db, rows)
    try:
        inline = await refresh_commit_health_after_update(sf, repo_id, str(repo), background=True)
        assert inline["exhausted"] == 1
        await drain_mod.drain(sf, repo_id, repo, total=600)
    finally:
        await engine.dispose()

    deltas = _rows(full_db, "git_commit_health_deltas")
    assert len(deltas) == len(rows)
    assert _rows(split_db, "git_commit_health_deltas") == deltas
    findings = _rows(full_db, "git_commit_health_findings")
    assert findings
    assert _rows(split_db, "git_commit_health_findings") == findings


@pytest.mark.parametrize(("stored", "update_running"), [(1, False), (0, False), (1, True)])
async def test_a_drain_that_stores_rows_writes_the_read_views_again(
    history, monkeypatch, stored, update_running
) -> None:
    """Its rows are an input of the read views, so storing them dropped the views."""
    repo, rows = history
    (repo / ".repowise").mkdir()
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    engine, _sf, repo_id = await _store(repo / ".repowise" / "wiki.db", rows)
    await engine.dispose()

    async def fake_drain(*_args, **_kwargs) -> int:
        return stored

    written: list[str] = []

    async def fake_write(_session, rid) -> list:
        written.append(rid)
        return []

    import repowise.core.persistence.crud.analysis.actions as actions

    monkeypatch.setattr(drain_mod, "drain", fake_drain)
    # An update that started meanwhile writes the views itself, from its final stores.
    monkeypatch.setattr(drain_mod, "_run_active", lambda _path: update_running)
    monkeypatch.setattr(actions, "write_read_snapshots", fake_write)
    await drain_mod._drain_store(repo, repo_id)
    assert written == ([repo_id] if stored and not update_running else [])
