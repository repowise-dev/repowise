"""``repowise init`` tombstones a page whose file is gone.

The helper doing the work is unit-tested next door. This drives the CLI
persistence path instead, because the helper existing is not the fix — being
called from both of ``persist_result``'s branches is. The normal single-repo
init persists the index phase during the run and so takes the
``index_persisted_incrementally=True`` branch, which never reached the
diff-driven tombstone path at all.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

from repowise.cli._repo_session import open_repo_db
from repowise.cli.commands.init_cmd.persistence import persist_result
from repowise.core.generation.models import GeneratedPage
from repowise.core.persistence import FullTextSearch, get_session
from repowise.core.persistence.crud import upsert_page
from repowise.core.persistence.models import Page, PageVersion, WikiSymbol
from repowise.core.persistence.vector_store import LanceDBVectorStore
from repowise.core.pipeline.cleanup_debt import load_cleanup_debt
from repowise.core.providers.embedding.base import MockEmbedder


def _generated_page(page_type: str, target: str) -> GeneratedPage:
    now = datetime.now(UTC).isoformat()
    return GeneratedPage(
        page_id=f"{page_type}:{target}",
        page_type=page_type,
        title=target,
        content=f"content for {target}",
        source_hash="x" * 64,
        model_name="mock",
        provider_name="mock",
        input_tokens=1,
        output_tokens=1,
        cached_tokens=0,
        generation_level=1,
        target_path=target,
        created_at=now,
        updated_at=now,
    )


def _result(repo_name: str, generated_pages: list[GeneratedPage]) -> SimpleNamespace:
    return SimpleNamespace(
        repo_name=repo_name,
        index_persisted_incrementally=True,
        parsed_files=[],
        file_infos=[SimpleNamespace(path="kept.py")],
        generated_pages=generated_pages,
        tech_stack=None,
        vector_store=None,
        dead_code_report=None,
        health_report=None,
        decision_report=None,
        git_metadata_list=[],
        knowledge_graph_result=None,
        authoritative_page_types=set(),
        preserved_page_ids=set(),
    )


async def _seed_file_pages(repo_path, *paths: str) -> str:
    """A fresh file page per path, seeded as a previous run would leave it."""
    engine, sf, repo_id = await open_repo_db(repo_path, repo_name="r")
    try:
        now = datetime.now(UTC)
        async with get_session(sf) as session:
            for path in paths:
                session.add(
                    Page(
                        id=f"file_page:{path}",
                        repository_id=repo_id,
                        page_type="file_page",
                        title=f"File: {path}",
                        content=f"# Overview\n\n{path} handles xylophone tuning.",
                        target_path=path,
                        source_hash="x" * 64,
                        model_name="mock",
                        provider_name="mock",
                        created_at=now,
                        updated_at=now,
                    )
                )
    finally:
        await engine.dispose()
    return repo_id


async def _status(repo_path, page_id: str) -> str:
    engine, sf, _ = await open_repo_db(repo_path, repo_name="r")
    try:
        async with get_session(sf) as session:
            res = await session.execute(select(Page).where(Page.id == page_id))
            return res.scalar_one().freshness_status
    finally:
        await engine.dispose()


async def test_init_tombstones_the_page_of_a_deleted_file(tmp_path):
    """No diff anywhere in this run — just a page whose file is not there."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "kept.py").write_text("x", encoding="utf-8")
    await _seed_file_pages(repo_path, "kept.py", "gone.py")

    await persist_result(_result("r", [_generated_page("module_page", "community-75")]), repo_path)

    assert await _status(repo_path, "file_page:gone.py") == "tombstone"
    assert await _status(repo_path, "file_page:kept.py") == "fresh"


async def test_the_tombstoned_page_leaves_the_full_text_index(tmp_path):
    """A tombstone still occupies one of the fixed number of rows retrieval
    fetches before any filter runs, so the row has to go, not just the flag."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "kept.py").write_text("x", encoding="utf-8")
    await _seed_file_pages(repo_path, "kept.py", "gone.py")

    engine, _sf, _ = await open_repo_db(repo_path, repo_name="r")
    fts = FullTextSearch(engine)
    await fts.ensure_index()
    for path in ("kept.py", "gone.py"):
        await fts.index(
            f"file_page:{path}",
            path,
            f"{path} handles xylophone tuning.",
            target_path=path,
            summary="xylophone tuning",
        )
    await engine.dispose()

    await persist_result(_result("r", [_generated_page("module_page", "community-75")]), repo_path)

    engine, _sf, _ = await open_repo_db(repo_path, repo_name="r")
    fts = FullTextSearch(engine)
    await fts.ensure_index()
    hits = {r.page_id for r in await fts.search("xylophone", limit=10)}
    await engine.dispose()

    assert "file_page:gone.py" not in hits
    assert "file_page:kept.py" in hits


@pytest.mark.parametrize("already_tombstoned", [False, True])
async def test_fast_reinit_reconciles_source_and_search_without_generating_pages(
    tmp_path, already_tombstoned
):
    """Retire excluded inputs, keep unreadable live inputs, and heal old search rows."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    for path in ("kept.py", "unparsed.py", "excluded.py"):
        (repo_path / path).write_text("x", encoding="utf-8")
    engine, sf, repo_id = await open_repo_db(repo_path, repo_name="r")
    paths = ("kept.py", "unparsed.py", "excluded.py", "gone.py")
    before = {}
    try:
        async with get_session(sf) as session:
            for path in paths:
                for revision in (1, 2):
                    await upsert_page(
                        session,
                        page_id=f"file_page:{path}",
                        repository_id=repo_id,
                        page_type="file_page",
                        title=path,
                        content=f"{path} handles xylophone tuning, revision {revision}.",
                        target_path=path,
                        source_hash="x" * 64,
                        provider_name="mock",
                        model_name="mock",
                    )
                session.add(
                    WikiSymbol(
                        repository_id=repo_id,
                        file_path=path,
                        symbol_id=f"{path}::tune",
                        name="tune",
                        qualified_name="tune",
                        kind="function",
                    )
                )
            for page in (await session.execute(select(Page))).scalars():
                before[page.id] = (page.content, page.provider_name, page.model_name, page.version)
                if already_tombstoned and page.target_path in {"gone.py", "excluded.py"}:
                    page.freshness_status = "tombstone"
        fts = FullTextSearch(engine)
        await fts.ensure_index()
        for path in paths:
            await fts.index(
                f"file_page:{path}",
                path,
                f"{path} handles xylophone tuning.",
                target_path=path,
                summary="xylophone tuning",
            )
    finally:
        await engine.dispose()
    store = LanceDBVectorStore(str(repo_path / ".repowise" / "lancedb"), MockEmbedder())
    await store.embed_batch([(f"file_page:{path}", path, {}) for path in paths])
    await store.close()
    result = _result("r", [])
    # Presence follows the walk, not the successful parse. excluded.py remains on disk.
    result.file_infos = [SimpleNamespace(path=path) for path in ("kept.py", "unparsed.py")]
    result.git_metadata_list = [
        {"file_path": path} for path in ("kept.py", "unparsed.py", "excluded.py")
    ]
    result.preserved_page_ids = {f"file_page:{path}" for path in paths}

    await persist_result(result, repo_path)

    # A successful repair must not reopen search stores on an unchanged rerun.
    async def redundant_cleanup(*args, **kwargs):
        raise AssertionError("retired search rows were already removed")

    with (
        patch.object(LanceDBVectorStore, "delete_many", redundant_cleanup),
        patch.object(FullTextSearch, "delete_many", redundant_cleanup),
    ):
        await persist_result(result, repo_path)

    engine, sf, _ = await open_repo_db(repo_path, repo_name="r")
    try:
        async with get_session(sf) as session:
            assert set((await session.execute(select(WikiSymbol.file_path))).scalars()) == {
                "kept.py",
                "unparsed.py",
            }
            pages = list((await session.execute(select(Page))).scalars())
            for page in pages:
                assert (page.content, page.provider_name, page.model_name, page.version) == before[
                    page.id
                ]
                assert page.freshness_status == (
                    "fresh" if page.target_path in {"kept.py", "unparsed.py"} else "tombstone"
                )
            versions = list((await session.execute(select(PageVersion))).scalars())
            assert len(versions) == len(paths)
            assert all("revision 1" in version.content for version in versions)
        fts = FullTextSearch(engine)
        assert {hit.page_id for hit in await fts.search("xylophone", limit=10)} == {
            "file_page:kept.py",
            "file_page:unparsed.py",
        }
    finally:
        await engine.dispose()
    # Reopen after a different connection committed the delete.
    reopened = LanceDBVectorStore(str(repo_path / ".repowise" / "lancedb"), MockEmbedder())
    try:
        assert await reopened.list_page_ids() == {"file_page:kept.py", "file_page:unparsed.py"}
    finally:
        await reopened.close()


@pytest.mark.parametrize("already_tombstoned", [False, True])
async def test_fast_reinit_retries_failed_search_cleanup(tmp_path, monkeypatch, already_tombstoned):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "kept.py").write_text("x", encoding="utf-8")
    await _seed_file_pages(repo_path, "kept.py", "gone.py")
    engine, sf, _ = await open_repo_db(repo_path, repo_name="r")
    try:
        if already_tombstoned:
            async with get_session(sf) as session:
                page = await session.get(Page, "file_page:gone.py")
                page.freshness_status = "tombstone"
        fts = FullTextSearch(engine)
        await fts.ensure_index()
        await fts.index(
            "file_page:gone.py",
            "gone",
            "xylophone tuning",
            target_path="gone.py",
            summary="xylophone tuning",
        )
    finally:
        await engine.dispose()
    delete_many = FullTextSearch.delete_many

    async def unavailable(self, page_ids):
        raise RuntimeError("search cleanup unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(FullTextSearch, "delete_many", unavailable)
        with pytest.raises(RuntimeError, match="search cleanup unavailable"):
            await persist_result(_result("r", []), repo_path)
    assert load_cleanup_debt(repo_path)["fts"] == {"file_page:gone.py"}
    assert FullTextSearch.delete_many is delete_many

    await persist_result(_result("r", []), repo_path)

    assert load_cleanup_debt(repo_path)["fts"] == set()
    assert await _status(repo_path, "file_page:gone.py") == "tombstone"
    engine, _, _ = await open_repo_db(repo_path, repo_name="r")
    try:
        assert await FullTextSearch(engine).search("xylophone") == []
    finally:
        await engine.dispose()


@pytest.mark.parametrize("already_tombstoned", [False, True])
async def test_fast_reinit_closes_delete_only_store_when_cleanup_fails(
    tmp_path, monkeypatch, already_tombstoned
):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "kept.py").write_text("x", encoding="utf-8")
    await _seed_file_pages(repo_path, "kept.py", "gone.py")
    if already_tombstoned:
        engine, sf, _ = await open_repo_db(repo_path, repo_name="r")
        try:
            async with get_session(sf) as session:
                page = await session.get(Page, "file_page:gone.py")
                page.freshness_status = "tombstone"
        finally:
            await engine.dispose()
    store = LanceDBVectorStore(str(repo_path / ".repowise" / "lancedb"), MockEmbedder())
    await store.embed_batch([("file_page:gone.py", "gone", {})])
    await store.close()
    opened = []

    async def unavailable(self, page_ids):
        await self._ensure_connected()
        opened.append(self)
        raise RuntimeError("vector cleanup unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(LanceDBVectorStore, "delete_many", unavailable)
        with pytest.raises(RuntimeError, match="vector cleanup unavailable"):
            await persist_result(_result("r", []), repo_path)

    assert len(opened) == 1
    assert opened[0]._db is None
    assert opened[0]._table is None
    assert await _status(repo_path, "file_page:gone.py") == (
        "tombstone" if already_tombstoned else "fresh"
    )
    assert load_cleanup_debt(repo_path)["vectors"] == {"file_page:gone.py"}

    await persist_result(_result("r", []), repo_path)
    assert await _status(repo_path, "file_page:gone.py") == "tombstone"
    assert load_cleanup_debt(repo_path)["vectors"] == set()
    reopened = LanceDBVectorStore(str(repo_path / ".repowise" / "lancedb"), MockEmbedder())
    try:
        assert await reopened.list_page_ids() == set()
    finally:
        await reopened.close()


async def test_legacy_cleanup_keeps_a_page_regenerated_in_this_init(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "kept.py").write_text("x", encoding="utf-8")
    await _seed_file_pages(repo_path, "kept.py")
    engine, sf, _ = await open_repo_db(repo_path, repo_name="r")
    try:
        async with get_session(sf) as session:
            page = await session.get(Page, "file_page:kept.py")
            page.freshness_status = "tombstone"
    finally:
        await engine.dispose()
    store = LanceDBVectorStore(str(repo_path / ".repowise" / "lancedb"), MockEmbedder())
    try:
        # Generation writes the new vector before final CLI persistence.
        await store.embed_batch([("file_page:kept.py", "regenerated content", {})])
        result = _result("r", [_generated_page("file_page", "kept.py")])
        result.vector_store = store

        await persist_result(result, repo_path)

        assert await _status(repo_path, "file_page:kept.py") == "fresh"
        assert await store.list_page_ids() == {"file_page:kept.py"}
    finally:
        await store.close()
