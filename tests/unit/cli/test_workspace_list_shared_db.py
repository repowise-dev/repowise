"""``workspace list`` must ask where the database is before reading a missing file.

Both the "is this repo indexed" check and ``_query_repo_counts`` were written
against the repo-local SQLite default: the first tests ``.repowise/`` for
existence, the second opens ``.repowise/wiki.db``. Under a configured shared
database (``REPOWISE_DB_URL``, as PostgreSQL deployments use) neither file
exists by design, so every member reads "not indexed" with zero counts no
matter how well indexed it is, and the summary line reports ``0/N``.

The output is worse than empty: the next step a reader draws from "not indexed"
is to reindex, against a database that already holds the pages.

These tests pin the three states the guard has to tell apart -- a configured
shared database, a repo-local file, and neither -- plus the summary line that
consumes the same counter.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from repowise.cli.main import cli

WORKSPACE_CONFIG_FILENAME = ".repowise-workspace.yaml"


def _write_workspace_config(root: Path, repos: list[dict], default_repo: str | None = None) -> None:
    """Write a minimal .repowise-workspace.yaml to *root*."""
    data: dict = {
        "version": 1,
        "default_repo": default_repo or (repos[0]["alias"] if repos else None),
        "repos": repos,
    }
    (root / WORKSPACE_CONFIG_FILENAME).write_text(
        yaml.dump(data, default_flow_style=False),
        encoding="utf-8",
    )


def _clear_db_env(monkeypatch) -> None:
    for name in ("REPOWISE_DB_URL", "REPOWISE_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)


def _configure_shared_db(monkeypatch, tmp_path: Path) -> Path:
    """Point both env vars at a store outside the repos, as PostgreSQL would be."""
    shared = tmp_path / "shared" / "index.db"
    shared.parent.mkdir(parents=True, exist_ok=True)
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite:///{shared.as_posix()}")
    return shared


@pytest.fixture
def runner():
    return CliRunner()


class TestWorkspaceListSharedDatabase:
    def test_configured_database_repo_is_not_reported_as_not_indexed(
        self, runner, tmp_path, monkeypatch
    ):
        """A shared database makes the repo a store with no repo-local file."""
        repo_a = tmp_path / "service-a"
        repo_a.mkdir()
        _write_workspace_config(tmp_path, repos=[{"path": "service-a", "alias": "service-a"}])

        _configure_shared_db(monkeypatch, tmp_path)

        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "not indexed" not in result.output

    def test_configured_database_counts_read_from_the_store(self, runner, tmp_path, monkeypatch):
        """Counts come from the configured store, not from a repo-local file."""
        repo_a = tmp_path / "service-a"
        repo_a.mkdir()
        _write_workspace_config(tmp_path, repos=[{"path": "service-a", "alias": "service-a"}])

        shared = _configure_shared_db(monkeypatch, tmp_path)

        # A store that knows the repository, with three files and seven symbols.
        _seed_store(shared, repo_a, file_count=3, symbol_count=7)

        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        assert result.exit_code == 0, result.output
        # The row carries the store's counts, not the "-" placeholders of the
        # unindexed row. Read them off the row rather than the whole output, so
        # a digit in a temp path cannot satisfy the assertion.
        row = next(line for line in result.output.splitlines() if "service-a" in line)
        assert " 3 " in row, row
        assert " 7 " in row, row
        assert "not indexed" not in result.output

    def test_summary_counts_a_configured_database_repo(self, runner, tmp_path, monkeypatch):
        """The summary line consumes the same counter as the rows."""
        repo_a = tmp_path / "service-a"
        repo_a.mkdir()
        _write_workspace_config(tmp_path, repos=[{"path": "service-a", "alias": "service-a"}])

        _configure_shared_db(monkeypatch, tmp_path)

        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "1/1 repos indexed" in result.output

    def test_no_configured_database_keeps_the_local_file_behaviour(
        self, runner, tmp_path, monkeypatch
    ):
        """With no configured URL, a repo without .repowise/ is still not indexed."""
        repo_a = tmp_path / "service-a"
        repo_a.mkdir()
        _write_workspace_config(tmp_path, repos=[{"path": "service-a", "alias": "service-a"}])

        _clear_db_env(monkeypatch)

        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "not indexed" in result.output
        assert "0/1 repos indexed" in result.output


def _seed_store(db_path: Path, repo_path: Path, *, file_count: int, symbol_count: int) -> None:
    """Create the store at *db_path* and give *repo_path* a graph with counts.

    ``node_id`` is NOT NULL and carries a unique path or symbol id per row, so
    each node gets its own; the counts this test asserts come from grouping by
    ``node_type``, the same query the command uses.
    """
    import asyncio

    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.models import GraphNode

    async def _seed() -> None:
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(
                session, name=repo_path.name, local_path=str(repo_path.resolve())
            )
            for i in range(file_count):
                session.add(
                    GraphNode(
                        repository_id=repo.id,
                        node_id=f"src/mod{i}.py",
                        node_type="file",
                    )
                )
            for i in range(symbol_count):
                session.add(
                    GraphNode(
                        repository_id=repo.id,
                        node_id=f"src/mod0.py::sym{i}",
                        node_type="symbol",
                    )
                )
            await session.commit()
        await engine.dispose()

    asyncio.run(_seed())
