"""``doctor --repair`` repairs a drifted full-text index (issue #1309).

0.38.0 widened ``page_fts`` from three columns to five. FTS5 cannot be
altered, so the index is dropped and refilled from ``wiki_pages`` the first
time an upgraded install opens the store. That rebuild used to refuse when the
index held more rows than ``wiki_pages`` could account for, and the error it
raised told the user to run ``repowise doctor --repair`` — which opens the
store the same way, hit the same refusal, and died before repairing anything.
``serve`` and the MCP server did too, so the whole wiki became unreachable.

The excess rows are orphans: pages swept from SQL whose index delete never
ran, because it runs after the commit, outside the transaction, best-effort.
They point at nothing, so discarding them is the repair.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from repowise.cli.commands.doctor_cmd import repo_checks

LIVE_PAGES = 24
ORPHANS = 89

_OLD_SCHEMA_DDL = "CREATE VIRTUAL TABLE page_fts USING fts5(page_id UNINDEXED, title, content)"


async def _build_drifted_repo(tmp_path: Path) -> Path:
    """A store shaped like the one in the report: 113 indexed rows, 24 pages."""
    import git as gitpython

    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import upsert_page, upsert_repository
    from repowise.core.persistence.database import init_db

    repo_path = (tmp_path / "repo").resolve()
    repo_path.mkdir()
    gitpython.Repo.init(repo_path)
    repowise_dir = repo_path / ".repowise"
    repowise_dir.mkdir()
    db_path = repowise_dir / "wiki.db"

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        repo = await upsert_repository(
            session, name="repo", local_path=str(repo_path), url="https://example.test/repo"
        )
        for i in range(LIVE_PAGES):
            await upsert_page(
                session,
                page_id=f"file_page:src/mod_{i}.py",
                repository_id=repo.id,
                page_type="file_page",
                title=f"File: src/mod_{i}.py",
                content=f"# Overview\n\nModule {i} does a substantial amount of work. " * 6,
                summary=f"Module {i}.",
                target_path=f"src/mod_{i}.py",
                source_hash="",
                model_name="mock",
                provider_name="mock",
            )
        await session.commit()
    await engine.dispose()

    # Rewind the index to the pre-0.38 shape and leave the orphans of sweeps
    # whose FTS delete never completed. Written through sqlite3 rather than the
    # ORM because no supported code path can produce this state any more.
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE page_fts")
    conn.execute(_OLD_SCHEMA_DDL)
    conn.executemany(
        "INSERT INTO page_fts(page_id, title, content) VALUES (?,?,?)",
        [(f"file_page:src/mod_{i}.py", f"mod_{i}", f"body {i}") for i in range(LIVE_PAGES)]
        + [(f"module:legacy/pkg_{i}", f"pkg_{i}", f"swept body {i}") for i in range(ORPHANS)],
    )
    conn.commit()
    conn.close()

    return repo_path


def _indexed_ids(repo_path: Path) -> set[str]:
    conn = sqlite3.connect(repo_path / ".repowise" / "wiki.db")
    try:
        return {r[0] for r in conn.execute("SELECT page_id FROM page_fts")}
    finally:
        conn.close()


def _fts_columns(repo_path: Path) -> list[str]:
    conn = sqlite3.connect(repo_path / ".repowise" / "wiki.db")
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(page_fts)")]
    finally:
        conn.close()


def test_the_drift_is_reported(tmp_path: Path) -> None:
    """The check runs on the old column set — it reads ``page_id`` only."""
    repo_path = asyncio.run(_build_drifted_repo(tmp_path))

    _all_ok, checks = repo_checks._run_repo_checks(repo_path, repair=False)
    rows = {c.name: (c.ok, c.detail) for c in checks}

    assert "SQL ↔ FTS Index" in rows
    ok, detail = rows["SQL ↔ FTS Index"]
    assert ok is False
    assert detail == f"0 missing, {ORPHANS} orphaned"


def test_repair_clears_the_orphans_instead_of_raising(tmp_path: Path) -> None:
    """The reported failure: the repair died on the drift it was called for."""
    repo_path = asyncio.run(_build_drifted_repo(tmp_path))

    repo_checks._run_repo_checks(repo_path, repair=True)

    assert _indexed_ids(repo_path) == {f"file_page:src/mod_{i}.py" for i in range(LIVE_PAGES)}


def test_repair_leaves_the_index_on_the_current_column_set(tmp_path: Path) -> None:
    """A repair that leaves the store one command away from the same wedge is
    not a repair. The schema upgrade is what the drift was blocking."""
    repo_path = asyncio.run(_build_drifted_repo(tmp_path))

    repo_checks._run_repo_checks(repo_path, repair=True)

    assert _fts_columns(repo_path) == [
        "page_id",
        "title",
        "content",
        "summary",
        "target_path",
    ]


def test_a_plain_run_heals_the_drift_on_its_own(tmp_path: Path) -> None:
    """Opening the store is enough. ``doctor`` is the recovery path, not the
    only one: every command runs ``ensure_index`` on the way in, which is where
    the orphans of an interrupted sweep now get collected."""
    from repowise.core.persistence import FullTextSearch, create_engine

    repo_path = asyncio.run(_build_drifted_repo(tmp_path))

    async def _open() -> None:
        engine = create_engine(
            f"sqlite+aiosqlite:///{repo_path / '.repowise' / 'wiki.db'}"
        )
        await FullTextSearch(engine).ensure_index()
        await engine.dispose()

    asyncio.run(_open())

    assert _indexed_ids(repo_path) == {f"file_page:src/mod_{i}.py" for i in range(LIVE_PAGES)}
    assert _fts_columns(repo_path)[-1] == "target_path"
