"""``repowise status`` must not present a broken index as healthy (issue #1748).

In the reported state every obvious signal was green: ``indexed: true``, a correct
``last_sync_commit``, and ``health.file_count`` of 2,251 — while only 9 files
resolved through the repository identity ingestion uses. The repository row itself
resolved correctly (that is how ``status`` printed those numbers), so a row-exists
check cannot catch it; what catches it is comparing the index's file rows against
the checkout they should describe.

These tests build real stores. An earlier version wrote a zero-byte ``wiki.db``,
which is not a valid SQLite file, so ``_mapping_report`` returned through its own
``except`` without ever running the query and the test passed without covering the
path it named.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from repowise.cli.commands import status_cmd


def _seed(repo: Path, *, file_nodes: int, repository_row: bool = True) -> None:
    """A real store at *repo* holding *file_nodes* file rows.

    ``repository_row=False`` writes the file rows under a *different* repository
    whose ``local_path`` does not match the checkout, which is the shape the
    reported failure produces: the index holds data, and none of it resolves.
    """
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.models import GraphNode, _new_uuid

    db_path = repo / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite+aiosqlite:///{db_path}"

    async def go() -> None:
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            name = repo.name if repository_row else "some-other-checkout"
            local = str(repo.resolve()) if repository_row else "/elsewhere/entirely"
            row = await upsert_repository(session, name=name, local_path=local)
            for i in range(file_nodes):
                session.add(
                    GraphNode(
                        id=_new_uuid(),
                        repository_id=row.id,
                        node_id=f"src/mod_{i}.py",
                        node_type="file",
                    )
                )
        await engine.dispose()

    asyncio.run(go())


def _checkout(tmp_path: Path, files: int) -> Path:
    """A checkout with *files* documentable files."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    for i in range(files):
        (repo / "src" / f"mod_{i}.py").write_text("x = 1\n", encoding="utf-8")
    return repo


def test_an_unindexed_directory_is_not_a_broken_mapping(tmp_path: Path) -> None:
    """The default this replaces was right, and stays: nothing to diverge from."""
    report = status_cmd._mapping_report(tmp_path)
    assert report["mapping_valid"] is True
    assert report["reason"] is None


def test_a_store_with_no_repository_row_is_invalid(tmp_path: Path) -> None:
    """``.repowise/`` exists but no row resolves the checkout."""
    repo = _checkout(tmp_path, 3)
    _seed(repo, file_nodes=0, repository_row=False)

    report = status_cmd._mapping_report(repo)
    assert report["mapping_valid"] is False
    assert "no repository row" in report["reason"]


def test_a_far_smaller_index_than_the_checkout_is_invalid(tmp_path: Path) -> None:
    """The reported failure: 9 file rows against a working tree of 2,251.

    This is the case a row-exists check cannot see, because the row resolves.
    """
    repo = _checkout(tmp_path, 40)
    _seed(repo, file_nodes=2)

    report = status_cmd._mapping_report(repo)
    assert report["mapping_valid"] is False
    assert report["indexed_files"] == 2
    assert report["working_tree_files"] == 40
    assert "resolve" in report["reason"]


def test_a_matching_index_is_valid(tmp_path: Path) -> None:
    """The normal case: the counts agree, so nothing is reported."""
    repo = _checkout(tmp_path, 12)
    _seed(repo, file_nodes=12)

    report = status_cmd._mapping_report(repo)
    assert report["mapping_valid"] is True
    assert report["reason"] is None
    assert report["indexed_files"] == 12
    assert report["working_tree_files"] == 12


def test_a_slightly_behind_index_is_still_valid(tmp_path: Path) -> None:
    """A lagging index is normal and must not be reported as broken.

    The ratio is deliberately loose for this reason: an index one edit behind is
    the ordinary state of a working repository.
    """
    repo = _checkout(tmp_path, 20)
    _seed(repo, file_nodes=19)

    assert status_cmd._mapping_report(repo)["mapping_valid"] is True


def test_the_single_repo_json_carries_the_preflight(tmp_path: Path, monkeypatch) -> None:
    """#1748 asks for a machine-readable check an automation can run first."""
    from click.testing import CliRunner

    from repowise.cli.main import cli

    repo = _checkout(tmp_path, 40)
    _seed(repo, file_nodes=2)
    (repo / ".repowise" / "state.json").write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(cli, ["status", str(repo), "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = __import__("json").loads(result.output)
    assert payload["mapping_valid"] is False
    assert payload["mapping"]["indexed_files"] == 2
    assert payload["mapping"]["working_tree_files"] == 40
