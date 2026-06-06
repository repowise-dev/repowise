"""Regression tests for the doctor SQL<->vector reconciliation.

The page vector store also holds *decision* embeddings under the
``decision:<record_id>`` namespace. The drift / orphan check used to compare
vector ids against ``wiki_pages`` only, so every decision embedding was counted
as an orphan vector — a false "Coordinator drift FAIL" on a consistent store,
and (with ``--repair``) a *destructive* deletion of valid decision vectors.

These tests pin the corrected behavior:
  * the SQL-side id set used for vector reconciliation is
    ``page_ids | {"decision:<id>"}``;
  * a genuinely orphaned vector (id in neither table) is still detected;
  * the repair deletion targets only the genuine orphan, never a decision.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.cli.commands.doctor_cmd import _decision_vector_ids
from repowise.core.analysis.decisions.semantic_match import DECISION_VECTOR_PREFIX
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
    session = factory()
    return engine, session


async def _insert_repo(session: AsyncSession):
    from repowise.core.persistence.crud import upsert_repository

    repo = await upsert_repository(
        session,
        name="taskq",
        local_path="/tmp/taskq",
        url="https://github.com/example/taskq",
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
async def test_decision_vector_ids_returns_namespaced_ids():
    engine, session = await _setup_session()
    try:
        repo = await _insert_repo(session)
        d1 = await _insert_decision(session, repo.id, "Adopt Redis")
        d2 = await _insert_decision(session, repo.id, "Use Postgres")

        ids = await _decision_vector_ids(session, repo.id)
        assert ids == {f"{DECISION_VECTOR_PREFIX}{d1}", f"{DECISION_VECTOR_PREFIX}{d2}"}
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_decisions_not_counted_as_orphans():
    """Consistent store: pages + decisions both embedded => zero orphan/drift."""
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

        page_ids = {"file_page:a.py", "file_page:b.py"}
        vector_sql_ids = page_ids | await _decision_vector_ids(session, repo.id)
        vs_ids = await vs.list_page_ids()

        # This mirrors the doctor reconciliation arithmetic.
        orphaned = vs_ids - vector_sql_ids
        missing = vector_sql_ids - vs_ids
        assert orphaned == set()
        assert missing == set()

        # Coordinator-style drift: SQL (pages + decisions) vs vector count.
        adjusted_sql = len(page_ids) + len(await _decision_vector_ids(session, repo.id))
        drift = abs(adjusted_sql - len(vs_ids)) / max(adjusted_sql, 1)
        assert drift == 0.0
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_genuine_orphan_still_detected():
    engine, session = await _setup_session()
    try:
        repo = await _insert_repo(session)
        await _insert_page(session, repo.id, "file_page:a.py")
        d1 = await _insert_decision(session, repo.id, "Adopt Redis")

        vs = InMemoryVectorStore(MockEmbedder())
        await vs.embed_and_upsert("file_page:a.py", "a", {})
        await vs.embed_and_upsert(f"{DECISION_VECTOR_PREFIX}{d1}", "redis", {})
        # A vector whose id is in neither wiki_pages nor decision_records.
        await vs.embed_and_upsert("file_page:ghost.py", "ghost", {})

        page_ids = {"file_page:a.py"}
        vector_sql_ids = page_ids | await _decision_vector_ids(session, repo.id)
        vs_ids = await vs.list_page_ids()

        orphaned = vs_ids - vector_sql_ids
        assert orphaned == {"file_page:ghost.py"}
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_repair_deletes_only_genuine_orphan_not_decisions():
    engine, session = await _setup_session()
    try:
        repo = await _insert_repo(session)
        await _insert_page(session, repo.id, "file_page:a.py")
        d1 = await _insert_decision(session, repo.id, "Adopt Redis")
        decision_vid = f"{DECISION_VECTOR_PREFIX}{d1}"

        vs = InMemoryVectorStore(MockEmbedder())
        await vs.embed_and_upsert("file_page:a.py", "a", {})
        await vs.embed_and_upsert(decision_vid, "redis", {})
        await vs.embed_and_upsert("file_page:ghost.py", "ghost", {})

        page_ids = {"file_page:a.py"}
        vector_sql_ids = page_ids | await _decision_vector_ids(session, repo.id)
        vs_ids = await vs.list_page_ids()
        orphaned = vs_ids - vector_sql_ids

        # Repair deletes exactly the orphan set (doctor's --repair loop).
        for pid in orphaned:
            await vs.delete(pid)

        surviving = await vs.list_page_ids()
        assert decision_vid in surviving, "decision embedding must survive repair"
        assert "file_page:a.py" in surviving
        assert "file_page:ghost.py" not in surviving
    finally:
        await session.close()
        await engine.dispose()
