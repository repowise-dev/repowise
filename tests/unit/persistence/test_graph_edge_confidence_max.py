"""``graph_edges`` upserts must keep the max confidence on collision.

A node pair can carry several resolved calls of differing confidence. The
in-memory resolver keeps the highest (``_resolvers.py:504-505``); the persistence
upsert historically did last-write, so a later low-confidence write could stamp a
real call below ``_FLOW_CALLS_CONF_FLOOR`` (0.5) and drop it from flow-path
answers. The upsert must mirror the resolver and keep the max.
"""

from __future__ import annotations

from sqlalchemy import select

from repowise.core.persistence import batch_upsert_graph_edges
from repowise.core.persistence.models import GraphEdge
from tests.unit.persistence.helpers import insert_repo


async def _edge_conf(session, repo_id: str) -> float:
    row = (
        await session.execute(select(GraphEdge).where(GraphEdge.repository_id == repo_id))
    ).scalar_one()
    return row.confidence


async def test_edge_upsert_keeps_max_confidence(async_session, tmp_path):
    repo = await insert_repo(async_session)
    edge = {"source_node_id": "a.py::f", "target_node_id": "b.py::g", "edge_type": "calls"}

    await batch_upsert_graph_edges(async_session, repo.id, [{**edge, "confidence": 0.9}])
    await async_session.commit()
    assert await _edge_conf(async_session, repo.id) == 0.9

    # A later, lower-confidence write for the same pair must not lower it below
    # the flow-path floor.
    await batch_upsert_graph_edges(async_session, repo.id, [{**edge, "confidence": 0.3}])
    await async_session.commit()
    assert await _edge_conf(async_session, repo.id) == 0.9

    # A higher-confidence write still wins.
    await batch_upsert_graph_edges(async_session, repo.id, [{**edge, "confidence": 0.95}])
    await async_session.commit()
    assert await _edge_conf(async_session, repo.id) == 0.95
