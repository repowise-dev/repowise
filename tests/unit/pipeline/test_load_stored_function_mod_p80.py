"""``load_stored_function_mod_p80`` reads the persisted per-function blame
rollup and returns the repo-wide p80 the incremental health pass reuses, so the
Function Hotspot gate is not biased by the changed-files subset (issue #1484).
"""

from __future__ import annotations

from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_session,
)
from repowise.core.persistence.crud import (
    get_repository_by_path,
    upsert_git_function_blame_bulk,
)
from repowise.core.persistence.database import init_db, resolve_db_url
from repowise.core.pipeline.incremental import load_stored_function_mod_p80


def _row(symbol_id: str, *, mods: int) -> dict:
    path, _, name = symbol_id.partition("::")
    return {
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


async def _seed(repo_path, rows: list[dict]) -> None:
    engine = create_engine(resolve_db_url(repo_path))
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                from repowise.core.persistence.crud import upsert_repository

                repo = await upsert_repository(
                    session,
                    name="loader-test",
                    local_path=str(repo_path),
                    url="https://example.test/loader",
                )
                await session.commit()
            await upsert_git_function_blame_bulk(session, repo.id, rows)
            await session.commit()
    finally:
        await engine.dispose()


async def test_loader_computes_full_repo_p80(tmp_path):
    """Full-repo distribution [1,2,2,3,4,4,5] → p80=4 (the issue's example).
    The changed-files subset [4,4,5] would give 5; the loader must answer 4."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    await _seed(
        repo_path,
        [
            _row("a.py::f1", mods=1),
            _row("a.py::f2", mods=2),
            _row("a.py::f3", mods=2),
            _row("b.py::f4", mods=3),
            _row("c.py::f5", mods=4),
            _row("c.py::f6", mods=4),
            _row("c.py::f7", mods=5),
        ],
    )

    assert await load_stored_function_mod_p80(repo_path) == 4


async def test_loader_no_store_returns_none(tmp_path):
    """Reading must never conjure a store, and an absent rollup is unknown."""
    assert await load_stored_function_mod_p80(tmp_path) is None
    assert not (tmp_path / ".repowise").exists()


async def test_loader_empty_rollup_returns_none(tmp_path):
    """No persisted blame rows (ESSENTIAL tier / never full-indexed) → None,
    so the analyzer falls back to the walked-set computation."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    engine = create_engine(resolve_db_url(repo_path))
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            from repowise.core.persistence.crud import upsert_repository

            await upsert_repository(
                session,
                name="loader-test",
                local_path=str(repo_path),
                url="https://example.test/loader",
            )
            await session.commit()
    finally:
        await engine.dispose()

    assert await load_stored_function_mod_p80(repo_path) is None
