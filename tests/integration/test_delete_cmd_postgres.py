"""``repowise delete`` against a real PostgreSQL store (issue #2320).

The bug: the command demanded a repository-local ``.repowise/wiki.db`` before it
resolved the configured database URL, so a repository whose index lives in a
shared PostgreSQL database (and which therefore has no local file at all) could
not be deleted.

``tests/integration/test_cli.py::TestDeleteWithConfiguredDb`` covers the same
code path against an external SQLite store, which is what the default test run
exercises. This file repeats the scenario on the dialect from the report, where
the FTS sweep is a no-op and the child rows go with the server's cascade.

Skipped unless a PostgreSQL URL is configured, so a local ``pytest`` run is
unchanged::

    REPOWISE_TEST_PG_URL=postgresql+asyncpg://user@localhost:5432/repowise_test \\
        uv run pytest tests/integration/test_delete_cmd_postgres.py
"""

from __future__ import annotations

import asyncio
import os

import pytest
from click.testing import CliRunner
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from repowise.cli.main import cli
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Base

PG_URL = os.environ.get("REPOWISE_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL, reason="REPOWISE_TEST_PG_URL not set — PostgreSQL-only regression"
)


def _seed(url: str, repo_path) -> None:
    """One repository at *repo_path*, with its own two pages."""
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        upsert_page,
        upsert_repository,
    )

    async def run() -> None:
        engine = create_engine(url)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(
                session, name=repo_path.name, local_path=str(repo_path.resolve())
            )
            for i in range(2):
                await upsert_page(
                    session,
                    # Scoped by repo so two seeded repositories cannot share a
                    # primary key and reassign each other's rows.
                    page_id=f"file_page:{repo_path.name}/module_{i}.py",
                    repository_id=repo.id,
                    page_type="file_page",
                    title=f"module_{i}.py",
                    content=f"# module {i}\n\nBody text for module {i}.",
                    target_path=f"src/module_{i}.py",
                    source_hash=f"hash-{i}",
                    model_name="mock",
                    provider_name="mock",
                )
        await engine.dispose()

    asyncio.run(run())


def _scalar(url: str, sql: str) -> int:
    async def run() -> int:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                return int((await conn.execute(text(sql))).scalar() or 0)
        finally:
            await engine.dispose()

    return asyncio.run(run())


@pytest.fixture
def pg_url(tmp_path, monkeypatch) -> str:
    """A scratch PostgreSQL database, exposed as ``REPOWISE_DB_URL``.

    The fixture drops every table, so it refuses a database that is not named
    like a scratch one. Pointing the env var at a real index and losing it
    should take more than a typo.
    """
    database = (PG_URL or "").rsplit("/", 1)[-1].split("?")[0]
    if "test" not in database and "scratch" not in database:
        pytest.skip(f"refusing to drop tables in {database!r}: name it *test* or *scratch*")

    async def reset() -> None:
        engine = create_async_engine(PG_URL or "")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await init_db(engine)
        await engine.dispose()

    asyncio.run(reset())
    monkeypatch.setenv("REPOWISE_DB_URL", PG_URL or "")
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    return PG_URL or ""


def test_delete_reaches_the_configured_postgres_store(pg_url, tmp_path):
    """No ``.repowise/`` anywhere; ``--path`` alone is enough to delete the repo."""
    repo_path = tmp_path / "example-repository"
    repo_path.mkdir()
    _seed(pg_url, repo_path)
    assert not (repo_path / ".repowise").exists()

    result = CliRunner().invoke(
        cli, ["delete", "--path", str(repo_path), "--force"], catch_exceptions=False
    )

    assert result.exit_code == 0, result.output
    assert "Database not found" not in result.output
    assert "No .repowise/ directory found" not in result.output
    assert "Deleted" in result.output
    assert _scalar(pg_url, "SELECT count(*) FROM repositories") == 0
    assert _scalar(pg_url, "SELECT count(*) FROM wiki_pages") == 0


def test_delete_path_leaves_other_repositories_in_place(pg_url, tmp_path):
    """The shared store keeps every row that ``--path`` did not name."""
    doomed = tmp_path / "doomed"
    keeper = tmp_path / "keeper"
    for repo_path in (doomed, keeper):
        repo_path.mkdir()
        _seed(pg_url, repo_path)

    result = CliRunner().invoke(
        cli, ["delete", "--path", str(doomed), "--force"], catch_exceptions=False
    )

    assert result.exit_code == 0, result.output
    assert "Enter number to delete" not in result.output
    assert _scalar(pg_url, "SELECT count(*) FROM repositories") == 1
    assert _scalar(pg_url, "SELECT count(*) FROM wiki_pages") == 2
