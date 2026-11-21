"""Readers keyed on the checkout path find an index built at another path.

An index built in one directory (another machine, a CI box) and copied to a
checkout elsewhere still names the old path in its repository row. ``generate``
then said the repo had no documentation and the AGENTS.md writer returned
None, although every page was there. Every reader resolves the row through
``get_repository_by_path``, which adopts the store's only row when git agrees
it is this checkout's, and never when it is another repo's.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from repowise.cli.commands.generate_cmd.engine import has_documentation
from repowise.cli.editor_files import write_agents_md
from repowise.cli.helpers import get_db_url_for_repo, repo_index_session
from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
    upsert_repository,
)
from repowise.core.persistence.crud import upsert_page

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.fixture(autouse=True)
def _no_configured_store(monkeypatch):
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)


def _git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=path,
        check=True,
        capture_output=True,
    )


def _git_repo(path: Path, message: str) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "a.py").write_text(f"# {message}\n")
    _git(path, "add", "a.py")
    _git(path, "commit", "-q", "-m", message)
    return path


async def _index(repo: Path) -> str:
    """A store in *repo* with its row and one page, as an index run leaves it."""
    (repo / ".repowise").mkdir()
    engine = create_engine(get_db_url_for_repo(repo))
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            row = await upsert_repository(session, name=repo.name, local_path=str(repo))
            await upsert_page(
                session,
                page_id=f"repo_overview:{repo.name}",
                repository_id=row.id,
                page_type="repo_overview",
                title="Overview",
                content="# overview",
                target_path=repo.name,
                source_hash="abc123",
                model_name="mock",
                provider_name="mock",
            )
            return row.id
    finally:
        await engine.dispose()


def _rows(repo: Path) -> list[tuple[str, str]]:
    with closing(sqlite3.connect(repo / ".repowise" / "wiki.db")) as conn:
        return conn.execute("SELECT id, local_path FROM repositories").fetchall()


@pytest.mark.parametrize("keep_original", [True, False], ids=["copied", "moved"])
async def test_generate_and_agents_md_read_an_index_built_elsewhere(
    tmp_path: Path, keep_original: bool
) -> None:
    built = _git_repo(tmp_path / "built" / "proj", "init")
    repo_id = await _index(built)
    here = tmp_path / "laptop" / "proj"
    shutil.copytree(built, here)
    if not keep_original:
        # The build path does not exist here, as with an index built on another machine.
        built.rename(tmp_path / "gone")

    assert await has_documentation(here) is True
    assert await write_agents_md(here) == here / "AGENTS.md"
    assert (here / "AGENTS.md").is_file()
    async with repo_index_session(here) as opened:
        assert opened is not None and opened[1] == repo_id
    # The row now names the checkout, so later lookups match it exactly.
    assert _rows(here) == [(repo_id, str(here))]


async def test_a_copy_that_moved_on_is_still_the_same_repo(tmp_path: Path) -> None:
    built = _git_repo(tmp_path / "built", "init")
    repo_id = await _index(built)
    here = tmp_path / "here"
    shutil.copytree(built, here)
    (here / "a.py").write_text("# next\n")
    _git(here, "commit", "-qam", "next")
    built.rename(tmp_path / "gone")

    assert await has_documentation(here) is True
    assert _rows(here) == [(repo_id, str(here))]


@pytest.mark.parametrize("keep_original", [True, False], ids=["old-path-live", "old-path-gone"])
async def test_another_repos_index_is_never_adopted(tmp_path: Path, keep_original: bool) -> None:
    other = _git_repo(tmp_path / "other", "other history")
    other_id = await _index(other)
    target = _git_repo(tmp_path / "target", "target history")
    shutil.copytree(other / ".repowise", target / ".repowise")
    if not keep_original:
        other.rename(tmp_path / "gone")

    assert await has_documentation(target) is False
    assert await write_agents_md(target) is None
    async with repo_index_session(target) as opened:
        assert opened is None
    # The other repo's row is left as it was, still naming its own path.
    assert _rows(target) == [(other_id, str(other))]


async def _lookup(repo: Path):
    from repowise.core.persistence.crud import get_repository_by_path

    engine = create_engine(get_db_url_for_repo(repo))
    try:
        async with get_session(create_session_factory(engine)) as session:
            row = await get_repository_by_path(session, str(repo))
            return None if row is None else row.id
    finally:
        await engine.dispose()


async def test_the_stored_path_misses_and_the_reader_still_finds_the_row(tmp_path: Path) -> None:
    """Negative control: the row names the build path, so an exact match misses."""
    built = _git_repo(tmp_path / "built", "init")
    repo_id = await _index(built)
    here = tmp_path / "here"
    shutil.copytree(built, here)
    built.rename(tmp_path / "gone")

    assert _rows(here) == [(repo_id, str(built))]
    assert str(here) not in {path for _, path in _rows(here)}
    assert await _lookup(here) == repo_id


async def test_a_rejected_row_is_not_rechecked(tmp_path: Path, monkeypatch) -> None:
    from repowise.core.persistence.crud import repository

    other = _git_repo(tmp_path / "other", "other history")
    await _index(other)
    target = _git_repo(tmp_path / "target", "target history")
    shutil.copytree(other / ".repowise", target / ".repowise")
    calls: list[str] = []
    real = repository._same_checkout

    def _counting(repo, local_path):
        calls.append(local_path)
        return real(repo, local_path)

    monkeypatch.setattr(repository, "_same_checkout", _counting)

    assert await _lookup(target) is None
    assert await _lookup(target) is None
    assert calls == [str(target)]


async def test_a_failed_adoption_reads_as_unindexed_and_moves_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    from sqlalchemy.exc import OperationalError

    from repowise.core.persistence.crud import repository

    built = _git_repo(tmp_path / "built", "init")
    repo_id = await _index(built)
    here = tmp_path / "here"
    shutil.copytree(built, here)
    ledger_moves: list[str] = []

    async def _locked(*_a, **_k):
        raise OperationalError("UPDATE", {}, Exception("database is locked"))

    async def _ledger(_session, local_path):
        ledger_moves.append(local_path)

    monkeypatch.setattr(repository, "rekey_repo_level_pages", _locked)
    monkeypatch.setattr(repository, "_adopt_savings_ledger", _ledger)

    assert await _lookup(here) is None
    assert ledger_moves == []
    assert _rows(here) == [(repo_id, str(built))]
