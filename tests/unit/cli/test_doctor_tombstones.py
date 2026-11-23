"""``doctor`` treats a tombstoned page as out of both indexes, as update does.

Update deletes a tombstone's full-text and vector rows, so its absence is the
correct state, and a row still holding one is an orphan. Counted as a live page
it read as missing, and ``--repair`` re-indexed and re-embedded pages for
deleted files.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from repowise.cli.commands.doctor_cmd import repo_checks

BODY = (
    "## Overview\n\n"
    "Resolves each import against the package manifest before descending, so "
    "a symlinked workspace member is visited once rather than once per alias "
    "that reaches it. Cycles break on the real path, never the alias.\n"
)
LIVE = "file_page:live.py"
GONE = "file_page:gone.py"  # tombstone, already out of both indexes
LEFT = "file_page:left.py"  # tombstone whose index rows were left behind


async def _build_repo(tmp_path: Path) -> Path:
    import git as gitpython

    from repowise.core.persistence import (
        FullTextSearch,
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import upsert_page, upsert_repository
    from repowise.core.persistence.database import init_db
    from repowise.core.persistence.vector_store import LanceDBVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder

    repo_path = (tmp_path / "repo").resolve()
    repo_path.mkdir()
    gitpython.Repo.init(repo_path)
    repowise_dir = repo_path / ".repowise"
    repowise_dir.mkdir()

    engine = create_engine(f"sqlite+aiosqlite:///{repowise_dir / 'wiki.db'}")
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        repo = await upsert_repository(
            session, name="repo", local_path=str(repo_path), url="https://example.test/repo"
        )
        for page_id, status in ((LIVE, "fresh"), (GONE, "tombstone"), (LEFT, "tombstone")):
            path = page_id.split(":", 1)[1]
            await upsert_page(
                session,
                page_id=page_id,
                repository_id=repo.id,
                page_type="file_page",
                title=f"File: {path}",
                content=BODY,
                summary="",
                target_path=path,
                source_hash="",
                model_name="mock",
                provider_name="mock",
                freshness_status=status,
            )
        await session.commit()

    fts = FullTextSearch(engine)
    await fts.ensure_index()
    store = LanceDBVectorStore(str(repowise_dir / "lancedb"), embedder=MockEmbedder())
    for page_id in (LIVE, LEFT):
        path = page_id.split(":", 1)[1]
        await fts.index(page_id, f"File: {path}", BODY, summary="", target_path=path)
        await store.embed_and_upsert(page_id, BODY, {"title": f"File: {path}"})
    await store.close()
    await engine.dispose()
    return repo_path


def _rows(repo_path: Path, repair: bool = False) -> dict[str, tuple[bool, str]]:
    _all_ok, checks = repo_checks._run_repo_checks(repo_path, repair=repair)
    return {c.name: (c.ok, c.detail) for c in checks}


def test_tombstones_are_never_missing_and_their_leftovers_are_orphans(tmp_path: Path) -> None:
    repo_path = asyncio.run(_build_repo(tmp_path))

    rows = _rows(repo_path)
    assert rows["SQL ↔ Vector Store"] == (False, "0 missing, 1 orphaned")
    assert rows["SQL ↔ FTS Index"] == (False, "0 missing, 1 orphaned")

    _rows(repo_path, repair=True)
    rows = _rows(repo_path)
    assert rows["SQL ↔ Vector Store"] == (True, "in sync")
    assert rows["SQL ↔ FTS Index"] == (True, "in sync")
