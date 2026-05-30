"""CRUD round-trip tests for the git_function_blame table."""

from __future__ import annotations

import pytest

from repowise.core.persistence.crud import (
    count_git_function_blame,
    delete_git_function_blame,
    get_git_function_blame,
    get_git_function_blames,
    upsert_git_function_blame_bulk,
)
from tests.unit.persistence.helpers import insert_repo


def _row(symbol_id: str, *, mods: int, **over) -> dict:
    path, _, name = symbol_id.partition("::")
    base = {
        "symbol_id": symbol_id,
        "file_path": path,
        "function_name": name,
        "start_line": 1,
        "end_line": 10,
        "line_count": 10,
        "mod_count": mods,
        "recent_mod_count": 1,
        "median_author_time": 1_700_000_000,
        "owner_name": "Ann",
        "owner_email": "ann@x",
        "owner_line_pct": 0.7,
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_upsert_and_rank_by_mods(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_git_function_blame_bulk(
        async_session,
        repo.id,
        [
            _row("a.py::foo", mods=2),
            _row("a.py::bar", mods=9),
            _row("b.py::baz", mods=5),
        ],
    )
    await async_session.commit()

    assert await count_git_function_blame(async_session, repo.id) == 3
    ranked = await get_git_function_blames(async_session, repo.id)
    # Hottest (most-modified) first.
    assert [r.symbol_id for r in ranked] == ["a.py::bar", "b.py::baz", "a.py::foo"]


@pytest.mark.asyncio
async def test_scope_by_file_path(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_git_function_blame_bulk(
        async_session,
        repo.id,
        [_row("a.py::foo", mods=2), _row("a.py::bar", mods=3), _row("b.py::baz", mods=5)],
    )
    await async_session.commit()

    only_a = await get_git_function_blames(async_session, repo.id, file_path="a.py")
    assert {r.symbol_id for r in only_a} == {"a.py::foo", "a.py::bar"}


@pytest.mark.asyncio
async def test_get_by_symbol_id_and_idempotent_upsert(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_git_function_blame_bulk(async_session, repo.id, [_row("a.py::foo", mods=2)])
    await async_session.commit()
    # Re-upsert same symbol with new mods → update, not duplicate.
    await upsert_git_function_blame_bulk(
        async_session, repo.id, [_row("a.py::foo", mods=7, owner_name="Bob")]
    )
    await async_session.commit()

    assert await count_git_function_blame(async_session, repo.id) == 1
    got = await get_git_function_blame(async_session, repo.id, "a.py::foo")
    assert got is not None
    assert got.mod_count == 7
    assert got.owner_name == "Bob"


@pytest.mark.asyncio
async def test_delete_git_function_blame(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_git_function_blame_bulk(async_session, repo.id, [_row("a.py::foo", mods=2)])
    await async_session.commit()
    await delete_git_function_blame(async_session, repo.id)
    await async_session.commit()
    assert await count_git_function_blame(async_session, repo.id) == 0
