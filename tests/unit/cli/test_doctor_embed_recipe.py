"""``doctor --repair`` re-embeds a missing page on the shared recipe.

It used to embed the bare content with neither the page's path nor its
summary, so a page whose vector this command restored came back weaker than
its neighbours — findable by fewer things than the page next to it, with the
store reporting itself repaired.

The repair runs deep inside ``_run_repo_checks``, so this drives the whole
command against a real store rather than reaching for the closure: a test
that cannot see the repair actually fire is not evidence the repair changed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from repowise.cli.commands.doctor_cmd import repo_checks
from repowise.core.persistence.vector_store import embed_item

PAGE_IN_STORE = "file_page:kept.py"
PAGE_MISSING = "file_page:packages/core/search.py"


class _RecordingStore:
    """Stands in for the store the repair writes through."""

    def __init__(self) -> None:
        self.items: list[tuple[str, str, dict]] = []

    async def embed_and_upsert(self, page_id: str, text: str, meta: dict) -> None:
        self.items.append((page_id, text, meta))

    async def delete(self, page_id: str) -> None:
        return None

    async def close(self) -> None:
        return None


async def _build_repo(tmp_path: Path, *, missing_title: str) -> Path:
    """A repo whose store holds one page and whose database holds two.

    The reconciliation only reports a missing page when the store is
    non-empty — an empty store reads as "not indexed yet", not as drift — so
    the kept page is what makes the missing one visible.
    """
    import git as gitpython

    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import upsert_page, upsert_repository
    from repowise.core.persistence.database import init_db
    from repowise.core.persistence.vector_store import LanceDBVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder

    # Resolved: on macOS tmp_path is a symlink, and the command looks the
    # repository up by the path it resolves to. An unresolved local_path
    # simply finds no repository and every reconciliation returns empty.
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
        common = {
            "repository_id": repo.id,
            "page_type": "file_page",
            "source_hash": "",
            "model_name": "mock",
            "provider_name": "mock",
        }
        await upsert_page(
            session,
            page_id=PAGE_IN_STORE,
            title="File: kept.py",
            content="Kept.",
            summary="",
            target_path="kept.py",
            **common,
        )
        await upsert_page(
            session,
            page_id=PAGE_MISSING,
            title=missing_title,
            content="## Overview\n\nBuilds the query and ranks the rows.",
            summary="Full-text search over the wiki index.",
            target_path="packages/core/search.py",
            **common,
        )
        await session.commit()
    await engine.dispose()

    # The real store, holding only the kept page.
    store = LanceDBVectorStore(str(repowise_dir / "lancedb"), embedder=MockEmbedder())
    await store.embed_and_upsert(PAGE_IN_STORE, "Kept.", {"title": "File: kept.py"})
    await store.close()

    return repo_path


@pytest.fixture
def repair(monkeypatch):
    """Run ``doctor --repair`` over the fixture repo; return what it embedded.

    Synchronous on purpose: the command drives its own event loop, so calling
    it from inside one raises before a single check runs.
    """

    def _run(repo_path: Path) -> _RecordingStore:
        recorder = _RecordingStore()
        monkeypatch.setattr("repowise.cli.providers.resolve_embedder_for_repo", lambda _p: "mock")
        monkeypatch.setattr("repowise.cli.providers.build_embedder", lambda _n, _p=None: object())
        monkeypatch.setattr("repowise.cli.providers.build_vector_store", lambda _p, _e: recorder)
        repo_checks._run_repo_checks(Path(repo_path), repair=True)
        return recorder

    return _run


def test_the_repair_embeds_the_shared_recipe(tmp_path: Path, repair) -> None:
    repo_path = asyncio.run(_build_repo(tmp_path, missing_title="File: packages/core/search.py"))

    recorder = repair(repo_path)

    assert recorder.items == [
        embed_item(
            PAGE_MISSING,
            title="File: packages/core/search.py",
            page_type="file_page",
            target_path="packages/core/search.py",
            summary="Full-text search over the wiki index.",
            content="## Overview\n\nBuilds the query and ranks the rows.",
        )
    ]


def test_the_repaired_vector_carries_the_path_and_summary(tmp_path: Path, repair) -> None:
    """Named directly, so the assertion does not rest on the helper alone."""
    repo_path = asyncio.run(_build_repo(tmp_path, missing_title="File: packages/core/search.py"))

    recorder = repair(repo_path)

    _pid, text, meta = recorder.items[0]
    assert "packages/core/search.py" in text
    assert "Full-text search over the wiki index." in text
    assert meta["summary"] == "Full-text search over the wiki index."


def test_a_titleless_page_is_left_reported_missing(tmp_path: Path, repair) -> None:
    """A repair that writes an unfindable row is not a repair.

    Better to leave the page reported as missing, where the next run says so
    again, than to fill the gap with a vector no search can reach by name.
    """
    repo_path = asyncio.run(_build_repo(tmp_path, missing_title=""))

    recorder = repair(repo_path)

    assert recorder.items == []
