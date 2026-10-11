"""Regression tests for ``AtomicStorageCoordinator.health_check`` (issue #374).

The page vector store co-locates wiki-page vectors and decision-record vectors
(the latter under the ``decision:<id>`` namespace). The health check used to
compare ``COUNT(*) FROM wiki_pages`` against the *total* vector count, so a
store holding only decision vectors reported ~100% drift even though the pages
themselves were fine. These tests pin the like-with-like comparison:

  * wiki_pages       <-> page vectors      -> page_drift
  * decision_records <-> decision vectors  -> decision_drift
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.analysis.decisions.semantic_match import DECISION_VECTOR_PREFIX
from repowise.core.persistence.coordinator import AtomicStorageCoordinator
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import DecisionRecord
from repowise.core.persistence.vector_store import InMemoryVectorStore
from repowise.core.providers.embedding.base import MockEmbedder


async def _setup_session() -> tuple[object, AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, factory()


async def _insert_repo(session: AsyncSession):
    from repowise.core.persistence.crud import upsert_repository

    repo = await upsert_repository(
        session, name="taskq", local_path="/tmp/taskq", url="https://github.com/example/taskq"
    )
    await session.commit()
    return repo


async def _insert_page(session: AsyncSession, repo_id: str, page_id: str) -> None:
    from repowise.core.persistence.crud import upsert_page

    await upsert_page(
        session,
        page_id=page_id,
        repository_id=repo_id,
        page_type="file_page",
        title=page_id,
        content="body",
        target_path=page_id,
        source_hash="h",
        model_name="mock",
        provider_name="mock",
        input_tokens=1,
        output_tokens=1,
    )
    await session.commit()


async def _insert_decision(session: AsyncSession, repo_id: str, title: str) -> str:
    rec = DecisionRecord(repository_id=repo_id, title=title, decision="because")
    session.add(rec)
    await session.commit()
    return rec.id


@pytest.mark.asyncio
async def test_consistent_store_reports_zero_drift_per_population():
    engine, session = await _setup_session()
    try:
        repo = await _insert_repo(session)
        await _insert_page(session, repo.id, "file_page:a.py")
        await _insert_page(session, repo.id, "file_page:b.py")
        d1 = await _insert_decision(session, repo.id, "Adopt Redis")

        vs = InMemoryVectorStore(MockEmbedder())
        await vs.embed_and_upsert("file_page:a.py", "a", {})
        await vs.embed_and_upsert("file_page:b.py", "b", {})
        await vs.embed_and_upsert(f"{DECISION_VECTOR_PREFIX}{d1}", "redis", {})

        coord = AtomicStorageCoordinator(session, graph_builder=None, vector_store=vs)
        report = await coord.health_check()

        assert report["sql_pages"] == 2
        assert report["sql_decisions"] == 1
        assert report["vector_count"] == 3
        assert report["vector_page_count"] == 2
        assert report["vector_decision_count"] == 1
        assert report["page_drift"] == 0.0
        assert report["decision_drift"] == 0.0
        assert report["drift"] == report["page_drift"]  # backwards-compat alias
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_only_decision_vectors_does_not_inflate_page_drift():
    """The issue #374 scenario: SQL has wiki pages, the store has only decision
    vectors. Page drift must reflect the missing *page* vectors (not be diluted
    by the decision vectors), and decision drift must read clean."""
    engine, session = await _setup_session()
    try:
        repo = await _insert_repo(session)
        await _insert_page(session, repo.id, "file_page:a.py")
        await _insert_page(session, repo.id, "file_page:b.py")
        d1 = await _insert_decision(session, repo.id, "Adopt Redis")

        vs = InMemoryVectorStore(MockEmbedder())
        # Only the decision vector exists — no page vectors were embedded.
        await vs.embed_and_upsert(f"{DECISION_VECTOR_PREFIX}{d1}", "redis", {})

        coord = AtomicStorageCoordinator(session, graph_builder=None, vector_store=vs)
        report = await coord.health_check()

        assert report["sql_pages"] == 2
        assert report["vector_page_count"] == 0
        assert report["vector_decision_count"] == 1
        # Page vectors are genuinely missing -> 100% page drift (reported clearly).
        assert report["page_drift"] == 1.0
        # Decisions are fully embedded -> no decision drift.
        assert report["decision_drift"] == 0.0
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_missing_decision_vectors_isolated_to_decision_drift():
    engine, session = await _setup_session()
    try:
        repo = await _insert_repo(session)
        await _insert_page(session, repo.id, "file_page:a.py")
        await _insert_decision(session, repo.id, "Adopt Redis")

        vs = InMemoryVectorStore(MockEmbedder())
        await vs.embed_and_upsert("file_page:a.py", "a", {})
        # Decision vector intentionally absent.

        coord = AtomicStorageCoordinator(session, graph_builder=None, vector_store=vs)
        report = await coord.health_check()

        assert report["page_drift"] == 0.0
        assert report["vector_decision_count"] == 0
        assert report["decision_drift"] == 1.0
    finally:
        await session.close()
        await engine.dispose()
