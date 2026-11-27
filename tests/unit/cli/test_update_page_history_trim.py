"""``update`` trims every page's history when a page is over the cap, and only then."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import func, select

from repowise.cli.commands.update_cmd.page_history import trim_page_history
from repowise.core.persistence import create_engine, create_session_factory, get_session
from repowise.core.persistence.crud import upsert_page, upsert_repository
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import PageVersion

PAGES = 3
WRITES = 15


@pytest.fixture(autouse=True)
def _local_store(monkeypatch):
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)


def _url(root: Path) -> str:
    return f"sqlite+aiosqlite:///{(root / '.repowise' / 'wiki.db').as_posix()}"


async def _seed(root: Path) -> None:
    """A store whose pages carry more history than the default cap, as before it existed."""
    (root / ".repowise").mkdir(parents=True, exist_ok=True)
    # A cap of 100 while seeding stands in for the unbounded history of older stores.
    (root / ".repowise" / "config.yaml").write_text(
        "wiki:\n  page_versions_keep: 100\n", encoding="utf-8"
    )
    engine = create_engine(_url(root))
    await init_db(engine)
    factory = create_session_factory(engine)
    async with get_session(factory) as session:
        repo = await upsert_repository(session, name="r", local_path=str(root))
        for p in range(PAGES):
            for i in range(WRITES):
                await upsert_page(
                    session,
                    page_id=f"file_page:m{p}.py",
                    repository_id=repo.id,
                    page_type="file_page",
                    title=f"m{p}",
                    content=f"body {i}",
                    target_path=f"m{p}.py",
                    source_hash=f"h{i}",
                    model_name="mock",
                    provider_name="mock",
                )
    await engine.dispose()
    (root / ".repowise" / "config.yaml").unlink()


async def _versions(root: Path) -> int:
    engine = create_engine(_url(root))
    async with engine.connect() as conn:
        count = (await conn.execute(select(func.count()).select_from(PageVersion))).scalar_one()
    await engine.dispose()
    return count


def _write_keep(root: Path, keep: object) -> None:
    (root / ".repowise" / "config.yaml").write_text(
        f"wiki:\n  page_versions_keep: {keep}\n", encoding="utf-8"
    )


def test_update_trims_old_history_and_again_whenever_a_page_is_over_the_cap(tmp_path):
    asyncio.run(_seed(tmp_path))
    assert asyncio.run(_versions(tmp_path)) == PAGES * (WRITES - 1)

    assert trim_page_history(tmp_path, dry_run=True) is None
    assert asyncio.run(_versions(tmp_path)) == PAGES * (WRITES - 1)

    assert trim_page_history(tmp_path, dry_run=False) == PAGES * (WRITES - 1 - 10)
    assert asyncio.run(_versions(tmp_path)) == PAGES * 10

    # Nothing over the cap: the next update deletes nothing.
    assert trim_page_history(tmp_path, dry_run=False) is None

    # Raising the cap deletes nothing; lowering it trims once more.
    _write_keep(tmp_path, 20)
    assert trim_page_history(tmp_path, dry_run=False) is None
    _write_keep(tmp_path, 4)
    assert trim_page_history(tmp_path, dry_run=False) == PAGES * 6
    assert asyncio.run(_versions(tmp_path)) == PAGES * 4


def test_history_grown_under_a_raised_cap_is_trimmed_after_it_drops_back(tmp_path):
    """A raise and a lower between two updates leave no page over the cap."""
    asyncio.run(_seed(tmp_path))
    assert trim_page_history(tmp_path, dry_run=False) == PAGES * (WRITES - 1 - 10)
    asyncio.run(_seed(tmp_path))  # more writes while the cap was 100
    _write_keep(tmp_path, 10)
    assert trim_page_history(tmp_path, dry_run=False) == PAGES * WRITES
    assert asyncio.run(_versions(tmp_path)) == PAGES * 10


def test_an_unreadable_cap_trims_nothing(tmp_path):
    asyncio.run(_seed(tmp_path))
    _write_keep(tmp_path, "lots")
    assert trim_page_history(tmp_path, dry_run=False) is None
    assert asyncio.run(_versions(tmp_path)) == PAGES * (WRITES - 1)


def test_no_store_means_nothing_runs(tmp_path):
    assert trim_page_history(tmp_path, dry_run=False) is None
