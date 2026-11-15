"""Markers stop re-scans, and the inline scan honours its budget, without changing a row.

Init then updates scan under a strict inline budget and leave large commits to
a full-budget drain. Once drained, the stored deltas, findings and markers must
equal one unbounded scan of the same history.
"""

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

#: Oldest first. The first two are the history ``init`` sees.
_VERSIONS = [
    {"a.py": "def one():\n    return 1\n"},
    {"a.py": "def one():\n    return 1\n" + _NESTED},
    # Docs only: compared, stores nothing.
    {"README.md": "# demo\n"},
    # Three code files: more than the inline budget ever fits.
    {
        "b.py": _NESTED.replace("tangle", "knot"),
        "c.py": _NESTED.replace("tangle", "snarl"),
        "d.py": "def d():\n    return 4\n",
    },
    # Over the per-commit file cap.
    {f"docs/{i}.md": f"# {i}\n" for i in range(6)},
    {"a.py": "def one():\n    return 1\n"},
]
_INIT_COMMITS = 2


@pytest.fixture
def history(tmp_path: Path, monkeypatch) -> tuple[Path, list[dict]]:
    # 1 analysed file is assumed to take 25 s against a 60 s budget: one- and
    # two-file commits fit, the three-file commit never does.
    monkeypatch.setattr(scan_mod, "SECONDS_PER_FILE", 25.0)
    monkeypatch.setenv("REPOWISE_COMMIT_HEALTH_BUDGET_SECONDS", "60")
    monkeypatch.setenv("REPOWISE_COMMIT_HEALTH_MAX_FILES", "5")
    monkeypatch.delenv(drain_mod.BACKGROUND_ENV, raising=False)
    monkeypatch.setattr(drain_mod, "start_background_drain", lambda *_args: None)
    repo = tmp_path / "repo"
    repo.mkdir()
    r = gitpython.Repo.init(repo, initial_branch="main")
    with r.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    rows = []
    for i, files in enumerate(_VERSIONS):
        for name, text in files.items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
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


async def _capture(sf, repo_id: str, rows: list[dict]) -> None:
    async with get_session(sf) as session:
        await upsert_git_commits_bulk(session, repo_id, rows)


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


def _outcomes(db: Path) -> dict[str, str]:
    conn = sqlite3.connect(db)
    try:
        return dict(conn.execute("SELECT sha, outcome FROM git_commit_health_scans").fetchall())
    finally:
        conn.close()


def _counting_compares(monkeypatch) -> list[str]:
    seen: list[str] = []
    real = scan_mod._compare_into

    def spy(scan, service, sha, pair, **kwargs):
        seen.append(sha)
        return real(scan, service, sha, pair, **kwargs)

    monkeypatch.setattr(scan_mod, "_compare_into", spy)
    return seen


async def test_init_and_updates_then_a_drain_store_what_one_full_scan_stores(
    history, tmp_path, monkeypatch
) -> None:
    repo, rows = history
    sha = [row["sha"] for row in rows]
    full_db, split_db = tmp_path / "full.db", tmp_path / "split.db"

    engine, sf, repo_id = await _store(full_db, rows)
    try:
        async with get_session(sf) as session:
            await refresh_commit_health(session, repo_id, str(repo), list(reversed(sha)))
    finally:
        await engine.dispose()

    compared = _counting_compares(monkeypatch)
    engine, sf, repo_id = await _store(split_db, rows[:_INIT_COMMITS])
    try:
        await refresh_commit_health_after_update(sf, repo_id, str(repo), background=True)
        for row in rows[_INIT_COMMITS:]:
            await _capture(sf, repo_id, [row])
            await refresh_commit_health_after_update(sf, repo_id, str(repo), background=True)

        # Each commit was reached once; the three-file one was never compared inline.
        assert sorted(compared) == sorted(s for s in sha if s not in (sha[3], sha[4]))
        assert _outcomes(split_db) == {
            sha[2]: "unavailable",
            sha[3]: "deferred",
            sha[4]: "too_large",
        }

        # Nothing new: no commit is compared again, not even the unstorable ones.
        compared.clear()
        again = await refresh_commit_health_after_update(sf, repo_id, str(repo), background=True)
        assert again["candidates"] == 0
        assert compared == []
        # The deferred commit still waits, so the update hands it to a drain again.
        assert again["exhausted"] == 1

        await drain_mod.drain(sf, repo_id, repo, total=600)
    finally:
        await engine.dispose()

    assert compared == [sha[3]]
    for table in (
        "git_commit_health_deltas",
        "git_commit_health_findings",
        "git_commit_health_scans",
    ):
        assert _rows(split_db, table) == _rows(full_db, table), table
    assert len(_rows(full_db, "git_commit_health_deltas")) == 4
    assert _rows(full_db, "git_commit_health_findings")
    assert _outcomes(full_db) == {sha[2]: "unavailable", sha[4]: "too_large"}


async def test_with_background_drains_off_the_inline_scan_still_reaches_large_commits(
    history, tmp_path, monkeypatch
) -> None:
    """No drain will come, so passing a commit over would leave it unscanned for good."""
    repo, rows = history
    monkeypatch.setenv(drain_mod.BACKGROUND_ENV, "0")
    compared = _counting_compares(monkeypatch)
    engine, sf, repo_id = await _store(tmp_path / "inline.db", rows)
    try:
        stats = await refresh_commit_health_after_update(sf, repo_id, str(repo), background=True)
    finally:
        await engine.dispose()
    assert rows[3]["sha"] in compared
    assert stats["stored"] == 4


def test_a_comparison_that_raised_leaves_no_marker_and_is_tried_again(tmp_path) -> None:
    """A git timeout or a lock is not a property of the commit."""

    class _Raises:
        calls = 0

        def compare(self, request):
            self.calls += 1
            raise TimeoutError("git timed out")

    service = _Raises()
    first = scan_mod.scan_commits(str(tmp_path), ["aaa"], service=service)
    assert (first.skipped, first.mark_rows, first.delta_rows) == (1, [], [])
