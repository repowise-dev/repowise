"""``doctor`` reconciles against every page, not the first ten thousand.

The SQL side of both store reconciliations was read with
``list_pages(session, repo.id, limit=10000)``. ``list_pages`` is the paginated
listing helper whose ``limit`` defaults to 100; passing 10000 does not remove
the cap, it raises it. Every page past the 10000th was invisible to the check,
so its vector and FTS row had no page behind them and was counted as an orphan
— on an 18900-page repository, 8900 phantom vector orphans and 3877 phantom
FTS ones, a permanent FAIL that no amount of reindexing moved.

``--repair`` deletes what this check reports. On any repository over the cap it
would therefore delete the live index it was asked to repair.

The same fetch backed the "Database" row, which reported exactly 10000 pages
for every repository larger than that.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from repowise.cli.commands.doctor_cmd import repo_checks

# One page past the old cap: the last page is the one that must stay visible.
PAGE_TOTAL = 10001
ORPHAN_CANARY = "file_page:beyond_the_cap.py"


async def _build_repo(tmp_path: Path) -> Path:
    """A repository with more pages than the old fetch could see.

    The canary is indexed in FTS and is the OLDEST page by ``updated_at``,
    which is what ``list_pages`` sorts on, descending — so under the old cap
    it was the first page dropped, and was reported as an orphan.
    """
    import datetime

    import git as gitpython
    from sqlalchemy import insert

    from repowise.core.persistence import (
        FullTextSearch,
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import upsert_repository
    from repowise.core.persistence.database import init_db
    from repowise.core.persistence.models import Page

    repo_path = (tmp_path / "repo").resolve()
    repo_path.mkdir()
    gitpython.Repo.init(repo_path)
    repowise_dir = repo_path / ".repowise"
    repowise_dir.mkdir()

    engine = create_engine(f"sqlite+aiosqlite:///{repowise_dir / 'wiki.db'}")
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        repo = await upsert_repository(
            session, name="repo", local_path=str(repo_path), url="https://example.test/repo"
        )
        # Bulk insert: the point is the row count, and 10001 upserts through the
        # CRUD layer would make this test slow enough that nobody runs it.
        base = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)

        def _stamp(i: int) -> datetime.datetime:
            return base + datetime.timedelta(seconds=i)

        rows = [
            {
                "created_at": _stamp(i),
                "updated_at": _stamp(i),
                "id": f"file_page:f{i}.py",
                "repository_id": repo.id,
                "page_type": "file_page",
                "title": f"File: f{i}.py",
                "content": "Body with enough words to clear the information floor.",
                "summary": "",
                "target_path": f"f{i}.py",
                "source_hash": "",
                "model_name": "mock",
                "provider_name": "mock",
                "freshness_status": "fresh",
            }
            for i in range(PAGE_TOTAL - 1)
        ]
        rows.append(
            {
                "created_at": base - datetime.timedelta(days=1),
                "updated_at": base - datetime.timedelta(days=1),
                "id": ORPHAN_CANARY,
                "repository_id": repo.id,
                "page_type": "file_page",
                "title": "File: beyond_the_cap.py",
                "content": "Body with enough words to clear the information floor.",
                "summary": "",
                "target_path": "beyond_the_cap.py",
                "source_hash": "",
                "model_name": "mock",
                "provider_name": "mock",
                "freshness_status": "fresh",
            }
        )
        await session.execute(insert(Page), rows)
        await session.commit()

    # Every page is indexed, so a healthy repository reports no drift in either
    # direction. That isolates what this test is about: the canary's FTS row has
    # a page behind it, and is only an "orphan" if the check cannot see it.
    fts = FullTextSearch(engine)
    await fts.ensure_index()
    await fts.index_many(
        [(r["id"], r["title"], r["content"], "", r["target_path"]) for r in rows]
    )
    await engine.dispose()
    return repo_path


def _rows(repo_path: Path) -> dict[str, tuple[bool, str]]:
    _all_ok, checks = repo_checks._run_repo_checks(repo_path, repair=False)
    return {c.name: (c.ok, c.detail) for c in checks}


def test_a_page_past_the_old_cap_is_not_an_orphan(tmp_path: Path) -> None:
    """The canary has a page behind it, so its FTS row is not drift."""
    rows = _rows(asyncio.run(_build_repo(tmp_path)))

    ok, detail = rows["SQL ↔ FTS Index"]
    assert detail == "in sync"
    assert ok is True


def test_the_database_row_counts_every_page(tmp_path: Path) -> None:
    """It reported exactly 10000 for any repository larger than that."""
    rows = _rows(asyncio.run(_build_repo(tmp_path)))

    _ok, detail = rows["Database"]
    assert detail == f"{PAGE_TOTAL} pages"
