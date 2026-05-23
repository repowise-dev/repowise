"""Shared contract tests for the pluggable persistence ABCs.

Every registered ``IndexStore`` / ``GraphStore`` / ``JobStore``
implementation runs through the same behavioral suite. Adding a new
backend means appending to the parametrise list at the top of each
section — no new test methods required for the common contract.

The OSS-only impls are exercised here: ``SqlIndexStore``,
``InProcessGraphStore``, ``SqlJobStore``. Third-party impls extend the
parametrisation in their own packages.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence._interfaces import JobState
from repowise.core.persistence.stores import (
    InProcessGraphStore,
    SqlIndexStore,
    SqlJobStore,
)
from tests.unit.persistence.helpers import make_repo_kwargs

# ---------------------------------------------------------------------------
# IndexStore — exercises the surface that crud.py exposes today
# ---------------------------------------------------------------------------


@pytest.fixture(params=["sql"])
def index_store(request, async_session: AsyncSession):
    """Parametrised over every IndexStore impl. Add new ids to params."""
    if request.param == "sql":
        return SqlIndexStore(async_session)
    raise AssertionError(f"Unknown IndexStore variant {request.param!r}")


@pytest.mark.asyncio
async def test_index_store_repository_roundtrip(index_store):
    """upsert → get_repository round-trips identical values."""
    repo = await index_store.upsert_repository(**make_repo_kwargs(name="contract-1"))
    fetched = await index_store.get_repository(repo.id)
    assert fetched is not None
    assert fetched.id == repo.id
    assert fetched.name == "contract-1"


@pytest.mark.asyncio
async def test_index_store_repository_by_path(index_store):
    """get_repository_by_path locates by local_path."""
    repo = await index_store.upsert_repository(
        **make_repo_kwargs(name="contract-2", local_path="/contract/path")
    )
    fetched = await index_store.get_repository_by_path("/contract/path")
    assert fetched is not None and fetched.id == repo.id


@pytest.mark.asyncio
async def test_index_store_session_property(async_session: AsyncSession):
    """SqlIndexStore exposes its bound session for raw-SQL escapes."""
    store = SqlIndexStore(async_session)
    assert store.session is async_session


@pytest.mark.asyncio
async def test_index_store_delete_returns_false_when_absent(index_store):
    """delete_repository returns False for an unknown id."""
    assert await index_store.delete_repository("does-not-exist") is False


# ---------------------------------------------------------------------------
# GraphStore — exercises the in-memory NetworkX wrapper
# ---------------------------------------------------------------------------


@pytest.fixture(params=["in_process"])
def graph_store(request):
    if request.param == "in_process":
        return InProcessGraphStore()
    raise AssertionError(f"Unknown GraphStore variant {request.param!r}")


def test_graph_store_basic_mutation(graph_store):
    graph_store.add_node("a.py", node_type="file")
    graph_store.add_node("b.py", node_type="file")
    graph_store.add_edge("a.py", "b.py", edge_type="imports")
    assert graph_store.has_node("a.py")
    assert graph_store.has_edge("a.py", "b.py")
    assert graph_store.node_count() == 2
    assert graph_store.edge_count() == 1


def test_graph_store_neighbors(graph_store):
    graph_store.add_node("a", node_type="file")
    graph_store.add_node("b", node_type="file")
    graph_store.add_node("c", node_type="file")
    graph_store.add_edge("a", "b")
    graph_store.add_edge("b", "c")
    assert graph_store.neighbors("b", direction="out") == ["c"]
    assert graph_store.neighbors("b", direction="in") == ["a"]
    assert set(graph_store.neighbors("b", direction="both")) == {"a", "c"}


def test_graph_store_metrics_on_empty_graph(graph_store):
    assert graph_store.pagerank() == {}
    assert graph_store.betweenness_centrality() == {}
    assert graph_store.communities() == {}


def test_graph_store_pagerank_after_build(graph_store):
    for node in ("a", "b", "c"):
        graph_store.add_node(node, node_type="file")
    graph_store.add_edge("a", "b")
    graph_store.add_edge("b", "c")
    graph_store.add_edge("a", "c")
    graph_store.build()
    pr = graph_store.pagerank()
    assert set(pr.keys()) == {"a", "b", "c"}
    assert all(isinstance(v, float) for v in pr.values())


def test_graph_store_invalidates_caches_on_mutation(graph_store):
    graph_store.add_node("a", node_type="file")
    graph_store.add_node("b", node_type="file")
    graph_store.add_edge("a", "b")
    _ = graph_store.pagerank()  # warm the cache
    graph_store.add_node("c", node_type="file")  # invalidate
    pr_after = graph_store.pagerank()
    assert "c" in pr_after


def test_graph_store_neighbors_invalid_direction(graph_store):
    graph_store.add_node("a", node_type="file")
    with pytest.raises(ValueError):
        graph_store.neighbors("a", direction="sideways")


# ---------------------------------------------------------------------------
# JobStore — exercises the pipeline_jobs checkpoint surface
# ---------------------------------------------------------------------------


@pytest.fixture(params=["sql"])
async def job_store(request, async_session: AsyncSession):
    if request.param == "sql":
        # SqlJobStore requires a real repository row because pipeline_jobs
        # has a FK on repositories.id.
        from repowise.core.persistence.stores import SqlIndexStore

        idx = SqlIndexStore(async_session)
        repo = await idx.upsert_repository(**make_repo_kwargs(name="job-host"))
        store = SqlJobStore(async_session)
        # Stash the repo id on the store for the tests that need it.
        store._test_repo_id = repo.id  # type: ignore[attr-defined]
        return store
    raise AssertionError(f"Unknown JobStore variant {request.param!r}")


@pytest.mark.asyncio
async def test_job_store_create_and_get(job_store):
    repo_id = job_store._test_repo_id  # type: ignore[attr-defined]
    job = await job_store.create_job(
        repository_id=repo_id, phase="parse", metadata={"k": 1}
    )
    assert job.state is JobState.PENDING
    assert job.phase == "parse"
    assert job.metadata == {"k": 1}
    fetched = await job_store.get_job(job.id)
    assert fetched is not None and fetched.id == job.id


@pytest.mark.asyncio
async def test_job_store_state_transitions(job_store):
    repo_id = job_store._test_repo_id  # type: ignore[attr-defined]
    job = await job_store.create_job(repository_id=repo_id, phase="graph")
    updated = await job_store.update_state(job.id, JobState.RUNNING)
    assert updated.state is JobState.RUNNING
    final = await job_store.update_state(
        job.id, JobState.FAILED, error="boom"
    )
    assert final.state is JobState.FAILED
    assert final.error == "boom"


@pytest.mark.asyncio
async def test_job_store_checkpoint_advances_cursor(job_store):
    repo_id = job_store._test_repo_id  # type: ignore[attr-defined]
    job = await job_store.create_job(repository_id=repo_id, phase="git")
    cp = await job_store.checkpoint(job.id, "abc/123")
    assert cp.cursor == "abc/123"
    cp = await job_store.checkpoint(job.id, "abc/456")
    assert cp.cursor == "abc/456"
    assert cp.state is JobState.PENDING  # checkpoint does not change state


@pytest.mark.asyncio
async def test_job_store_find_resumable_filters_by_state(job_store):
    repo_id = job_store._test_repo_id  # type: ignore[attr-defined]
    a = await job_store.create_job(repository_id=repo_id, phase="a")
    b = await job_store.create_job(repository_id=repo_id, phase="b")
    await job_store.update_state(a.id, JobState.COMPLETED)
    await job_store.update_state(b.id, JobState.RUNNING)
    resumable = await job_store.find_resumable(repository_id=repo_id)
    ids = {j.id for j in resumable}
    assert b.id in ids
    assert a.id not in ids


@pytest.mark.asyncio
async def test_job_store_get_unknown_returns_none(job_store):
    assert await job_store.get_job("does-not-exist") is None
