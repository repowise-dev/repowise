"""Round-trip smoke tests for the batch upserts routed through ``_batch_upsert``.

These guard the generic SELECT-then-INSERT/UPDATE helper that
``crud/_shared.py`` introduced when ``crud.py`` was split into a package: every
batch upsert must still insert on first write and update in place on the second,
and the batched git upsert must converge across the 500-row chunk boundary.
``test_crud.py`` already covers the graph-node/edge/symbol insert paths; this
file fills the gaps (metrics, git bulk, the update paths, and batching).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from repowise.core.persistence.crud import (
    batch_upsert_graph_edges,
    batch_upsert_graph_metrics,
    batch_upsert_symbols,
    get_all_git_metadata,
    get_graph_metrics,
    upsert_git_metadata_bulk,
)
from repowise.core.persistence.models import GraphEdge, WikiSymbol
from tests.unit.persistence.helpers import insert_repo


class _Sym:
    """Duck-typed stand-in for ingestion.models.Symbol."""

    def __init__(self, file_path: str, name: str, kind: str = "function", **extra):
        self.file_path = file_path
        self.name = name
        self.kind = kind
        self.id = f"{file_path}::{name}"
        for k, v in extra.items():
            setattr(self, k, v)


@pytest.mark.asyncio
async def test_batch_upsert_graph_metrics_inserts_then_updates(async_session):
    repo = await insert_repo(async_session)
    await batch_upsert_graph_metrics(
        async_session,
        repo.id,
        {"a.py": {"pagerank": 0.5, "betweenness": 0.1, "community_id": 2, "in_degree": 3}},
    )
    got = await get_graph_metrics(async_session, repo.id)
    assert got["a.py"]["pagerank"] == 0.5
    assert got["a.py"]["community_id"] == 2
    assert got["a.py"]["in_degree"] == 3

    # Second write updates in place — only supplied fields change.
    await batch_upsert_graph_metrics(
        async_session, repo.id, {"a.py": {"pagerank": 0.9, "out_degree": 7}}
    )
    got = await get_graph_metrics(async_session, repo.id)
    assert got["a.py"]["pagerank"] == 0.9  # updated
    assert got["a.py"]["out_degree"] == 7  # updated
    assert got["a.py"]["community_id"] == 2  # preserved (not in second payload)
    # exactly one row for the node_id
    rows = await get_graph_metrics(async_session, repo.id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_batch_upsert_graph_edges_updates_existing(async_session):
    repo = await insert_repo(async_session)
    edges = [{"source_node_id": "a", "target_node_id": "b", "edge_type": "imports"}]
    await batch_upsert_graph_edges(async_session, repo.id, edges)
    # Re-upsert the same key with new optional fields → in-place update.
    await batch_upsert_graph_edges(
        async_session,
        repo.id,
        [
            {
                "source_node_id": "a",
                "target_node_id": "b",
                "edge_type": "imports",
                "imported_names_json": '["foo"]',
                "confidence": 0.5,
            }
        ],
    )
    result = await async_session.execute(
        select(GraphEdge).where(GraphEdge.repository_id == repo.id)
    )
    rows = list(result.scalars().all())
    assert len(rows) == 1
    assert rows[0].imported_names_json == '["foo"]'
    assert rows[0].confidence == 0.5


@pytest.mark.asyncio
async def test_batch_upsert_symbols_updates_existing(async_session):
    repo = await insert_repo(async_session)
    await batch_upsert_symbols(async_session, repo.id, [_Sym("m.py", "f", signature="()")])
    await batch_upsert_symbols(
        async_session, repo.id, [_Sym("m.py", "f", signature="(x)", kind="method")]
    )
    result = await async_session.execute(
        select(WikiSymbol).where(WikiSymbol.repository_id == repo.id)
    )
    rows = list(result.scalars().all())
    assert len(rows) == 1
    assert rows[0].signature == "(x)"
    assert rows[0].kind == "method"


@pytest.mark.asyncio
async def test_upsert_git_metadata_bulk_inserts_updates_and_batches(async_session):
    repo = await insert_repo(async_session)
    # 1200 rows spans the 500-row chunk boundary (flush per chunk).
    rows = [{"file_path": f"f{i}.py", "commit_count_total": 1} for i in range(1200)]
    await upsert_git_metadata_bulk(async_session, repo.id, rows)
    got = await get_all_git_metadata(async_session, repo.id)
    assert len(got) == 1200
    assert got["f0.py"].commit_count_total == 1

    # Re-upsert one path → in-place update, no duplicate row.
    await upsert_git_metadata_bulk(
        async_session, repo.id, [{"file_path": "f0.py", "commit_count_total": 9}]
    )
    got = await get_all_git_metadata(async_session, repo.id)
    assert len(got) == 1200
    assert got["f0.py"].commit_count_total == 9


@pytest.mark.asyncio
async def test_upsert_git_metadata_bulk_empty_is_noop(async_session):
    repo = await insert_repo(async_session)
    await upsert_git_metadata_bulk(async_session, repo.id, [])
    assert await get_all_git_metadata(async_session, repo.id) == {}
