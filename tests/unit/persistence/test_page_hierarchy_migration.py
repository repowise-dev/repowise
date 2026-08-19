"""An existing wiki store gains the hierarchy columns without a manual step.

Local stores are never migrated by Alembic: ``init_db`` reconciles the live
schema against the ORM model and issues the missing ALTER TABLEs itself. That
is the path every existing OSS user takes, so it is the one worth testing.
Alembic covers the hosted Postgres deployments, which run it explicitly.

The store here is built and then stripped back to its pre-hierarchy shape, so
the test exercises a real upgrade rather than a fresh create.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.crud import get_page, upsert_page, upsert_repository
from repowise.core.persistence.database import init_db

_HIERARCHY_COLUMNS = ("parent_page_id", "display_order", "section_number", "structural_key")

_PAGE_ID = "file_page:src/app.py"


async def _columns(engine) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(text("PRAGMA table_info(wiki_pages)"))
        return {r[1] for r in rows.all()}


@pytest.fixture
async def legacy_store():
    """A store holding one page, with the hierarchy columns removed again."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)

    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sf() as session:
        repo = await upsert_repository(
            session, name="r", local_path="/tmp/r", url="https://example.com/r"
        )
        repo_id = repo.id
        await upsert_page(
            session,
            page_id=_PAGE_ID,
            repository_id=repo_id,
            page_type="file_page",
            title="app.py",
            content="Body.",
            target_path="src/app.py",
            source_hash="h" * 64,
            model_name="mock",
            provider_name="mock",
        )
        await session.commit()

    # Roll the schema back to its pre-hierarchy shape. The indexes go first:
    # SQLite refuses to drop a column an index still references.
    async with engine.begin() as conn:
        await conn.execute(text("DROP INDEX IF EXISTS ix_wiki_pages_parent_page_id"))
        await conn.execute(text("DROP INDEX IF EXISTS ix_wiki_pages_structural_key"))
        for column in _HIERARCHY_COLUMNS:
            await conn.execute(text(f"ALTER TABLE wiki_pages DROP COLUMN {column}"))

    yield engine, repo_id
    await engine.dispose()


async def test_the_fixture_really_produced_a_legacy_store(legacy_store):
    """Otherwise the upgrade below would be testing a fresh create."""
    engine, _ = legacy_store
    assert set(_HIERARCHY_COLUMNS).isdisjoint(await _columns(engine))


async def test_init_db_adds_the_hierarchy_columns_to_an_existing_store(legacy_store):
    engine, _ = legacy_store
    await init_db(engine)
    assert set(_HIERARCHY_COLUMNS) <= await _columns(engine)


async def test_the_existing_page_survives_the_upgrade(legacy_store):
    """A backfilled row must still be readable, and read as unplaced.

    Null parent and order zero is the honest description of a page written
    before the wiki had a tree, and it is what keeps the page serving instead
    of 404ing while the rollout is half-done.
    """
    engine, _ = legacy_store
    await init_db(engine)

    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sf() as session:
        page = await get_page(session, _PAGE_ID)

    assert page is not None
    assert page.title == "app.py"
    assert page.parent_page_id is None
    assert page.display_order == 0
    assert page.section_number is None
    assert page.structural_key is None


async def test_an_upgraded_store_accepts_hierarchy_writes(legacy_store):
    engine, repo_id = legacy_store
    await init_db(engine)

    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sf() as session:
        await upsert_page(
            session,
            page_id=_PAGE_ID,
            repository_id=repo_id,
            page_type="file_page",
            title="app.py",
            content="Body.",
            target_path="src/app.py",
            source_hash="h" * 64,
            model_name="mock",
            provider_name="mock",
            parent_page_id="module_page:src",
            display_order=2,
            section_number="1.3",
            structural_key="grp-0123456789ab",
        )
        await session.commit()

    async with sf() as session:
        page = await get_page(session, _PAGE_ID)

    assert page.parent_page_id == "module_page:src"
    assert page.display_order == 2
    assert page.section_number == "1.3"
    assert page.structural_key == "grp-0123456789ab"


async def test_reconciler_is_idempotent(legacy_store):
    """Every CLI command calls init_db, so it runs constantly."""
    engine, _ = legacy_store
    await init_db(engine)
    await init_db(engine)
    assert set(_HIERARCHY_COLUMNS) <= await _columns(engine)
