"""End-to-end fast→full upgrade: rehydrate + backfill + generate, no re-resolve.

Runs the real pipeline against the sample repo with ``--mode fast`` (no LLM),
persists it, then exercises the incremental upgrade path:

* the dependency graph is rehydrated from SQL and is structurally + metric-
  equivalent to the fast build;
* the git tier is backfilled ESSENTIAL → FULL via the resumable worker, and the
  JobStore records a COMPLETED ``git.backfill`` checkpoint;
* docs are generated (mock LLM) against the rehydrated graph;
* **no graph resolution runs during the upgrade** — ``GraphBuilder.build`` is
  spied and asserted to be called zero times after the fast index.

No external services; the only LLM is the deterministic ``MockProvider``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.ingestion.git_indexer import GitIndexer, GitIndexTier
from repowise.core.ingestion.git_indexer.backfill import BACKFILL_PHASE, backfill_full_tier
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.persistence import init_db, upsert_repository
from repowise.core.persistence._interfaces.job_store import JobState
from repowise.core.persistence.stores.sql_job_store import SqlJobStore
from repowise.core.pipeline import (
    persist_pipeline_result,
    rehydrate_graph_builder,
    run_generation,
    run_pipeline,
)
from repowise.core.pipeline.modes import OrchestratorMode
from repowise.core.providers.llm.mock import MockProvider


@pytest.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_fast_index_then_full_upgrade(
    sample_repo_path: Path,
    session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # --- 1. Fast index + persist (ESSENTIAL git, no docs). ---
    repo = await upsert_repository(session, name="sample", local_path=str(sample_repo_path))
    repo_id = repo.id

    result = await run_pipeline(sample_repo_path, mode=OrchestratorMode.FAST)
    assert result.generated_pages is None  # FAST never generates docs
    assert result.git_metadata_list, "expected git metadata for the sample repo"
    assert all(
        m.get("co_change_partners_json", "[]") == "[]" for m in result.git_metadata_list
    ), "ESSENTIAL tier should not compute co-change"

    await persist_pipeline_result(result, session, repo_id)
    await session.commit()

    original_nodes = result.graph_builder.graph().number_of_nodes()
    original_edges = result.graph_builder.graph().number_of_edges()
    original_pagerank = result.graph_builder.pagerank()

    # --- 2. Forbid graph resolution for the rest of the test. Any rebuild of
    # the graph during the upgrade is a regression — the persisted graph must
    # be reused as-is. ---
    build_calls = {"n": 0}
    real_build = GraphBuilder.build

    def _spy_build(self, *args, **kwargs):
        build_calls["n"] += 1
        return real_build(self, *args, **kwargs)

    monkeypatch.setattr(GraphBuilder, "build", _spy_build)

    # --- 3. Rehydrate the graph from SQL — equivalence + zero resolution. ---
    rehydrated = await rehydrate_graph_builder(session, repo_id, sample_repo_path)
    assert rehydrated.graph().number_of_nodes() == original_nodes
    assert rehydrated.graph().number_of_edges() == original_edges
    assert rehydrated.pagerank() == original_pagerank
    assert build_calls["n"] == 0, "rehydration must not re-resolve the graph"

    # --- 4. Backfill the git tier ESSENTIAL -> FULL (resumable via JobStore). ---
    indexer = GitIndexer(sample_repo_path, tier=GitIndexTier.FULL)
    job_store = SqlJobStore(session)
    summary, git_results = await backfill_full_tier(indexer, repo_id, job_store=job_store)
    await session.commit()

    backfill_jobs = await job_store.list_jobs(repository_id=repo_id, phase=BACKFILL_PHASE)
    assert backfill_jobs, "backfill should record a JobStore checkpoint"
    assert backfill_jobs[0].state is JobState.COMPLETED
    assert summary.files_indexed >= 0

    # --- 5. Generate docs against the rehydrated graph (mock LLM). The jobs
    # dir is routed to tmp_path so the sample fixture stays clean. ---
    git_meta_map = {m["file_path"]: m for m in git_results if m.get("file_path")}
    pages = await run_generation(
        repo_path=tmp_path,
        parsed_files=result.parsed_files,
        source_map=result.source_map,
        graph_builder=rehydrated,
        repo_structure=result.repo_structure,
        git_meta_map=git_meta_map,
        llm_client=MockProvider(),
        embedder=None,
        vector_store=None,
        concurrency=2,
        progress=None,
    )

    assert pages, "the upgrade should generate the docs the fast index skipped"
    # The entire upgrade (rehydrate + backfill + generate) ran no graph build.
    assert build_calls["n"] == 0, "the upgrade must not re-resolve the graph"
