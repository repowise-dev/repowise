"""``doctor`` does not report a deliberately unindexed page as drift.

A page below the information floor is held out of both indexes on purpose,
so its absence is the correct state. Counting it as missing makes every run
report drift that ``--repair`` cannot fix — the repair applies the same rule
and declines — which is the permanent noise the decision namespace is
already excluded from the MISSING check to avoid.

It stays on the ORPHAN side. A stored vector for a page that is now below
the floor is real drift, and deleting it is a repair that works.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from repowise.cli.commands.doctor_cmd import repo_checks
from repowise.core.persistence.information_floor import INFORMATION_FLOOR_ENV

THICK = "file_page:thick.py"
THIN = "file_page:thin.py"

# Stripped to nothing by the floor: a heading and a metadata strip.
THIN_BODY = "# thin.py\n\n**Kind:** module | **Defined in:** `thin.py`\n"
THICK_BODY = (
    "## Overview\n\n"
    "Resolves each import against the package manifest before descending, so "
    "a symlinked workspace member is visited once rather than once per alias "
    "that reaches it. Cycles break on the real path, never the alias, which "
    "is why two aliases of one directory cannot both claim to own a module. "
    "The manifest is read once per package and cached for the walk.\n"
)


async def _build_repo(tmp_path: Path) -> Path:
    """Both pages in the database; only the thick one in either index."""
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
        for page_id, path, body in (
            (THICK, "thick.py", THICK_BODY),
            (THIN, "thin.py", THIN_BODY),
        ):
            await upsert_page(
                session,
                page_id=page_id,
                repository_id=repo.id,
                page_type="file_page",
                title=f"File: {path}",
                content=body,
                summary="",
                target_path=path,
                source_hash="",
                model_name="mock",
                provider_name="mock",
            )
        await session.commit()

    fts = FullTextSearch(engine)
    await fts.ensure_index()
    await fts.index(THICK, "File: thick.py", THICK_BODY, summary="", target_path="thick.py")
    await engine.dispose()

    store = LanceDBVectorStore(str(repowise_dir / "lancedb"), embedder=MockEmbedder())
    await store.embed_and_upsert(THICK, THICK_BODY, {"title": "File: thick.py"})
    await store.close()

    return repo_path


def _rows(repo_path: Path) -> dict[str, tuple[bool, str]]:
    _all_ok, checks = repo_checks._run_repo_checks(repo_path, repair=False)
    return {c.name: (c.ok, c.detail) for c in checks}


def test_a_page_below_the_floor_is_not_reported_as_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(INFORMATION_FLOOR_ENV, "300")
    rows = _rows(asyncio.run(_build_repo(tmp_path)))

    assert rows["SQL ↔ Vector Store"] == (True, "in sync")
    assert rows["SQL ↔ FTS Index"] == (True, "in sync")


def test_the_same_page_is_drift_when_the_floor_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exclusion has to follow the floor, not the page.

    With the floor off, that page belongs in both indexes and its absence is
    ordinary drift the repair can and should fix.
    """
    monkeypatch.delenv(INFORMATION_FLOOR_ENV, raising=False)
    rows = _rows(asyncio.run(_build_repo(tmp_path)))

    assert rows["SQL ↔ Vector Store"] == (False, "1 missing, 0 orphaned")
    assert rows["SQL ↔ FTS Index"] == (False, "1 missing, 0 orphaned")
