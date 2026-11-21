"""Unit tests for repowise delete exit codes (issue #5980)."""

from __future__ import annotations

import asyncio

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli
from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
    upsert_repository,
)


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def indexed_repo(tmp_path, monkeypatch):
    """A temporary repo initialized with wiki.db and one registered repository."""
    repo_dir = tmp_path / "sample_repo"
    repo_dir.mkdir()
    repowise_dir = repo_dir / ".repowise"
    repowise_dir.mkdir()
    db_path = repowise_dir / "wiki.db"

    # Make sure env doesn't point to another db
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)

    url = f"sqlite+aiosqlite:///{db_path}"

    async def _init():
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            await upsert_repository(session, name="sample_repo", local_path=str(repo_dir.resolve()))
        await engine.dispose()

    asyncio.run(_init())
    return {"repo_dir": repo_dir, "db_path": db_path}


def test_delete_missing_database_exits_1(runner, tmp_path, monkeypatch):
    """When .repowise/ exists but wiki.db is missing, delete must report and exit 1."""
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)

    repo_dir = tmp_path / "empty_repo"
    repo_dir.mkdir()
    (repo_dir / ".repowise").mkdir()

    result = runner.invoke(cli, ["delete", "--path", str(repo_dir), "--force"])
    assert result.exit_code == 1, result.output
    assert "Database not found." in result.output


def test_delete_unknown_id_exits_1(runner, indexed_repo):
    """Deleting a non-existent repo id must report not found and exit 1."""
    repo_dir = indexed_repo["repo_dir"]
    result = runner.invoke(
        cli,
        ["delete", "non-existent-id-12345", "--path", str(repo_dir), "--force"],
    )
    assert result.exit_code == 1, result.output
    assert "Repository non-existent-id-12345 not found." in result.output


def test_delete_unmatched_path_exits_1(runner, indexed_repo, tmp_path, monkeypatch):
    """Deleting with a --path that matches no row in configured DB must report and exit 1."""
    db_path = indexed_repo["db_path"]
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db_path}")

    unindexed = tmp_path / "never_indexed"
    unindexed.mkdir()

    result = runner.invoke(
        cli,
        ["delete", "--path", str(unindexed), "--force"],
    )
    assert result.exit_code == 1, result.output
    assert "No repository in the database matches" in result.output


def test_delete_invalid_selection_exits_1(runner, indexed_repo, monkeypatch):
    """Interactive prompt with invalid index input must report invalid selection and exit 1."""
    db_path = indexed_repo["db_path"]
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db_path}")

    result = runner.invoke(
        cli,
        ["delete"],
        input="999\n",
    )
    assert result.exit_code == 1, result.output
    assert "Invalid selection." in result.output
