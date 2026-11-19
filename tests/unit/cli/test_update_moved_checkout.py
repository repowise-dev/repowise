"""``repowise update`` in a moved or copied checkout keeps one repository row.

A copied checkout carries its repo-local store, which still names the old
``local_path``. Update keys its repository row on the path it runs in, so an
exact-path lookup missed and every ``upsert_repository`` inserted a second
row; the MCP server then served the old row's tree. Update now adopts the
store's only row first, by the same rule the CLI read side applies.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from repowise.cli.helpers import adopt_moved_repo_best_effort, get_db_url_for_repo
from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
    upsert_repository,
)
from repowise.core.persistence.crud import adopt_moved_repository
from repowise.core.persistence.models import Repository


@pytest.fixture(autouse=True)
def _no_configured_store(monkeypatch):
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)


async def _init_store(root: Path, extra_paths: tuple[str, ...] = ()) -> None:
    (root / ".repowise").mkdir(parents=True)
    engine = create_engine(get_db_url_for_repo(root))
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            await upsert_repository(session, name=root.name, local_path=str(root))
            for extra in extra_paths:
                await upsert_repository(session, name=Path(extra).name, local_path=extra)
    finally:
        await engine.dispose()


async def _rows(root: Path) -> list[tuple[str, str]]:
    engine = create_engine(get_db_url_for_repo(root))
    try:
        async with get_session(create_session_factory(engine)) as session:
            repos = (await session.scalars(select(Repository).order_by(Repository.name))).all()
            return [(r.id, r.local_path) for r in repos]
    finally:
        await engine.dispose()


async def _update_style_upserts(root: Path) -> None:
    """What the update path does after resolving: upsert by the new path."""
    engine = create_engine(get_db_url_for_repo(root))
    try:
        async with get_session(create_session_factory(engine)) as session:
            await upsert_repository(session, name=root.name, local_path=str(root))
            await upsert_repository(session, name=root.name, local_path=str(root))
    finally:
        await engine.dispose()


async def test_copied_checkout_keeps_one_row_pointing_at_the_copy(tmp_path):
    orig, moved = tmp_path / "orig", tmp_path / "moved"
    await _init_store(orig)
    shutil.copytree(orig, moved)
    ((orig_id, _),) = await _rows(orig)

    assert await adopt_moved_repo_best_effort(moved) is True
    await _update_style_upserts(moved)

    assert await _rows(moved) == [(orig_id, str(moved))]
    # The original checkout's own store is untouched.
    assert await _rows(orig) == [(orig_id, str(orig))]


async def test_without_adoption_the_update_would_add_a_second_row(tmp_path):
    """Pins the failure the adoption step exists to prevent."""
    orig, moved = tmp_path / "orig", tmp_path / "moved"
    await _init_store(orig)
    shutil.copytree(orig, moved)

    await _update_style_upserts(moved)

    assert len(await _rows(moved)) == 2


async def test_already_current_checkout_is_left_alone(tmp_path):
    root = tmp_path / "repo"
    await _init_store(root)
    before = await _rows(root)

    assert await adopt_moved_repo_best_effort(root) is False
    assert await _rows(root) == before


async def test_store_with_two_rows_matches_by_exact_path_only(tmp_path):
    orig, moved = tmp_path / "orig", tmp_path / "moved"
    await _init_store(orig, extra_paths=(str(tmp_path / "other"),))
    shutil.copytree(orig, moved)
    before = await _rows(moved)

    assert await adopt_moved_repo_best_effort(moved) is False
    assert await _rows(moved) == before


async def test_configured_store_matches_by_exact_path_only(tmp_path, monkeypatch):
    orig, moved = tmp_path / "orig", tmp_path / "moved"
    await _init_store(orig)
    shutil.copytree(orig, moved)
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{moved / '.repowise' / 'wiki.db'}")
    before = await _rows(moved)

    async with get_session(
        create_session_factory(create_engine(get_db_url_for_repo(moved)))
    ) as session:
        assert await adopt_moved_repository(session, str(moved)) is None
    assert await adopt_moved_repo_best_effort(moved) is False
    assert await _rows(moved) == before


async def test_missing_store_is_not_created(tmp_path):
    root = tmp_path / "never_indexed"
    root.mkdir()

    assert await adopt_moved_repo_best_effort(root) is False
    assert not (root / ".repowise").exists()


def test_update_adopts_before_any_write_and_not_on_dry_run(tmp_path, monkeypatch):
    from repowise.cli.commands.update_cmd import command as cmd

    calls: list[Path] = []

    class _Stop(Exception):
        pass

    async def _adopt(root: Path) -> bool:
        calls.append(root)
        return True

    def _stop(*_a, **_k):
        raise _Stop

    monkeypatch.setattr(cmd, "adopt_moved_repo_best_effort", _adopt)
    monkeypatch.setattr(cmd, "_start_run", lambda opts: SimpleNamespace(repo_path=None))
    monkeypatch.setattr(
        cmd,
        "_resolve_target",
        lambda opts: SimpleNamespace(is_workspace=False, repo_path=tmp_path),
    )
    monkeypatch.setattr(cmd, "_prepare_repo", lambda p: None)
    monkeypatch.setattr(cmd, "_full_upgrade", _stop)
    monkeypatch.setattr(cmd, "_load_sync_base", _stop)

    def _run(dry_run: bool) -> None:
        with pytest.raises(_Stop):
            cmd.run_update(
                path=str(tmp_path),
                provider_name=None,
                model=None,
                since=None,
                reasoning=None,
                cascade_budget=None,
                dry_run=dry_run,
                workspace=False,
                no_workspace=True,
                repo_alias=None,
            )

    _run(dry_run=True)
    assert calls == []
    _run(dry_run=False)
    assert calls == [tmp_path]
