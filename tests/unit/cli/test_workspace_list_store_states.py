"""The three store states ``workspace list`` has to tell apart, at unit tier.

#2403 fixed this in ``tests/integration/test_cli.py``, which CI runs on push to
main and not on pull requests, so a regression here would first be seen after
merge. These pin the same decision where the PR gate actually looks.

The decision under test:

    counts  = _query_repo_counts(abs_path)
    indexed = counts is not None if configured_db else repowise_dir.exists()

``_query_repo_counts`` returns ``None`` only when no store could answer for the
path. A returned ``(0, 0)`` means a store answered and holds nothing, which
under a configured database still means the repository is indexed. Collapsing
those two would report a repository the store has no row for as indexed, with a
``0 files`` row that reads as a broken index rather than a missing one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from repowise.cli.main import cli

WORKSPACE_CONFIG_FILENAME = ".repowise-workspace.yaml"


def _write_workspace_config(root: Path, repos: list[dict], default_repo: str | None = None) -> None:
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
    """Point the configured URL at a store outside the repos, as PostgreSQL would be."""
    shared = tmp_path / "shared" / "index.db"
    shared.parent.mkdir(parents=True, exist_ok=True)
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite:///{shared.as_posix()}")
    return shared


def _seed_store(db_path: Path, repo_path: Path, *, file_count: int, symbol_count: int) -> None:
    """Create the store at *db_path* with a repository row and graph counts."""
    import asyncio

    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.models import GraphNode, WikiSymbol

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
            # Symbols are counted from ``wiki_symbols``, not from graph nodes of
            # type "symbol", which is what the surfaces' "symbols" figure means.
            for i in range(symbol_count):
                session.add(
                    WikiSymbol(
                        repository_id=repo.id,
                        file_path="src/mod0.py",
                        symbol_id=f"src/mod0.py::sym{i}",
                        name=f"sym{i}",
                        qualified_name=f"sym{i}",
                        kind="function",
                    )
                )
            await session.commit()
        await engine.dispose()

    asyncio.run(_seed())


@pytest.fixture
def runner():
    return CliRunner()


def _row_for(output: str, label: str) -> str:
    """The rendered table line for *label*, so a neighbour's cell cannot satisfy an assert."""
    for line in output.splitlines():
        if label in line:
            return line
    raise AssertionError(f"no table row for {label!r} in:\n{output}")


class TestWorkspaceListStoreStates:
    """A repository the store knows, one it does not, and no store at all."""

    def test_a_row_in_the_shared_store_is_indexed_with_its_counts(
        self, runner, tmp_path, monkeypatch
    ):
        """The reported bug: an indexed member with no local file must not read bare."""
        repo_a = tmp_path / "service-a"
        repo_a.mkdir()
        _write_workspace_config(tmp_path, repos=[{"path": "service-a", "alias": "service-a"}])

        shared = _configure_shared_db(monkeypatch, tmp_path)
        _seed_store(shared, repo_a, file_count=3, symbol_count=7)

        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        assert result.exit_code == 0, result.output

        row = _row_for(result.output, "service-a")
        assert " 3 " in row, row
        assert " 7 " in row, row
        assert "not indexed" not in row
        assert "1/1 repos indexed" in result.output

    def test_a_path_the_store_does_not_hold_is_still_not_indexed(
        self, runner, tmp_path, monkeypatch
    ):
        """A configured database is not itself the verdict.

        The store answers for the path or it does not. This is the case the
        ``(0, 0)`` return value cannot express, and the reason the function
        returns ``None`` instead: a member the store has never seen must not be
        reported as indexed with a zero count.
        """
        repo_a = tmp_path / "service-a"
        repo_a.mkdir()
        repo_b = tmp_path / "service-b"
        repo_b.mkdir()
        _write_workspace_config(
            tmp_path,
            repos=[
                {"path": "service-a", "alias": "service-a"},
                {"path": "service-b", "alias": "service-b"},
            ],
            default_repo="service-a",
        )

        shared = _configure_shared_db(monkeypatch, tmp_path)
        # Only service-a gets a row. service-b stays unknown to the store.
        _seed_store(shared, repo_a, file_count=3, symbol_count=5)

        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        assert result.exit_code == 0, result.output

        row_a = _row_for(result.output, "service-a")
        row_b = _row_for(result.output, "service-b")
        assert "not indexed" not in row_a
        assert "not indexed" in row_b
        assert "1/2 repos indexed" in result.output

    def test_a_store_error_is_an_error_not_the_not_indexed_verdict(
        self, runner, tmp_path, monkeypatch
    ):
        """A store that fails is not a store with no row.

        ``_query_store_counts`` once wrapped the query in a bare
        ``except Exception``, so an unreachable or corrupt store returned the
        same ``None`` as a missing repository row — and under a configured
        database ``None`` is the not-indexed verdict. A fully indexed member
        would report "not indexed" during an outage, the misreport this file
        exists to pin. A store error must surface as an error.
        """
        repo_a = tmp_path / "service-a"
        repo_a.mkdir()
        _write_workspace_config(tmp_path, repos=[{"path": "service-a", "alias": "service-a"}])

        shared = _configure_shared_db(monkeypatch, tmp_path)
        shared.write_bytes(b"this is not a sqlite database")

        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        assert result.exit_code != 0, result.output
        assert "not indexed" not in result.output
        assert "index store" in result.output

    def test_one_bad_store_does_not_stop_the_listing(self, runner, tmp_path, monkeypatch):
        """A member whose store fails still lets the others print.

        ``workspace_list`` used to call ``_query_store_counts`` bare in the
        member loop, so the first unreadable store aborted the command before
        any row reached the table. The healthy members are the point of the
        listing; the broken one gets its own row and the error after it.
        """
        repo_a = tmp_path / "service-a"
        repo_a.mkdir()
        (repo_a / ".repowise").mkdir()
        (repo_a / ".repowise" / "wiki.db").write_bytes(b"this is not a sqlite database")
        repo_b = tmp_path / "service-b"
        repo_b.mkdir()
        _write_workspace_config(
            tmp_path,
            repos=[
                {"path": "service-a", "alias": "service-a"},
                {"path": "service-b", "alias": "service-b"},
            ],
        )
        _clear_db_env(monkeypatch)

        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        assert result.exit_code != 0, result.output
        row_a = _row_for(result.output, "service-a")
        row_b = _row_for(result.output, "service-b")
        assert "store unreadable" in row_a
        assert "not indexed" in row_b
        assert "index store" in result.output

    def test_a_missing_store_file_is_not_indexed_and_not_created(
        self, runner, tmp_path, monkeypatch
    ):
        """A read command must never materialise a store.

        With a configured SQLite URL whose file does not exist,
        ``create_engine`` would create it the moment it opened the URL — and
        the resulting table-less database then read as a store error instead
        of the unindexed store it is. The member is unindexed there: no file,
        no error, no row.
        """
        repo_a = tmp_path / "service-a"
        repo_a.mkdir()
        _write_workspace_config(tmp_path, repos=[{"path": "service-a", "alias": "service-a"}])

        shared = _configure_shared_db(monkeypatch, tmp_path)

        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        assert result.exit_code == 0, result.output
        row = _row_for(result.output, "service-a")
        assert "not indexed" in row
        assert not shared.exists()

    def test_an_initialised_store_with_no_tables_is_not_indexed(
        self, runner, tmp_path, monkeypatch
    ):
        """An existing but empty database is unindexed, not a store error."""
        repo_a = tmp_path / "service-a"
        repo_a.mkdir()
        _write_workspace_config(tmp_path, repos=[{"path": "service-a", "alias": "service-a"}])

        shared = _configure_shared_db(monkeypatch, tmp_path)
        shared.write_bytes(b"")  # the file exists; nothing ever wrote schema

        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        assert result.exit_code == 0, result.output
        row = _row_for(result.output, "service-a")
        assert "not indexed" in row

    def test_without_a_configured_database_the_local_directory_decides(
        self, runner, tmp_path, monkeypatch
    ):
        """No configured URL keeps the repo-local default exactly as it was."""
        repo_a = tmp_path / "service-a"
        repo_a.mkdir()
        _write_workspace_config(tmp_path, repos=[{"path": "service-a", "alias": "service-a"}])

        _clear_db_env(monkeypatch)

        result = runner.invoke(cli, ["workspace", "list", str(tmp_path)])
        assert result.exit_code == 0, result.output

        row = _row_for(result.output, "service-a")
        assert "not indexed" in row
        assert "0/1 repos indexed" in result.output
