"""The CLI's plan fallback validates from the checkout, so JS/TS commands run from their package."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.cli.commands.health_cmd.persist import _load_recommendations
from repowise.cli.helpers import run_async
from repowise.core.persistence.crud.analysis import refactoring_recommendations


async def _store_repo(repo_path: Path) -> None:
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )

    (repo_path / ".repowise").mkdir()
    engine = create_engine(
        f"sqlite+aiosqlite:///{(repo_path / '.repowise' / 'wiki.db').as_posix()}"
    )
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            await upsert_repository(session, name="repo", local_path=str(repo_path))
    finally:
        await engine.dispose()


def test_the_cli_fallback_passes_its_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    root = tmp_path.resolve()
    run_async(_store_repo(root))
    seen: dict[str, object] = {}

    async def hydrate(session, repository_id, rows, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(refactoring_recommendations, "hydrate_recommendations", hydrate)
    assert _load_recommendations(root, [], []) == []
    assert seen["checkout"] == str(root)
