"""graph_node_membership survives the persistence round trip.

Locks the materializer ``batch_upsert_graph_node_membership`` and the
``get_scc_members`` reader so the SCC + symbol-community snapshot stays
queryable.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.crud.graph import (
    batch_upsert_graph_node_membership,
    get_scc_members,
)
from tests.unit.persistence.helpers import insert_repo


@pytest.mark.asyncio
async def test_membership_round_trip_and_scc_read(async_session):
    repo = await insert_repo(async_session)
    membership = {
        "a.py": {"node_type": "file", "scc_id": 0, "scc_size": 2, "symbol_community_id": None},
        "b.py": {"node_type": "file", "scc_id": 0, "scc_size": 2, "symbol_community_id": None},
        "m.py::C.x": {
            "node_type": "symbol",
            "scc_id": None,
            "scc_size": 0,
            "symbol_community_id": 7,
        },
    }
    await batch_upsert_graph_node_membership(async_session, repo.id, membership)
    await async_session.commit()

    sccs = await get_scc_members(async_session, repo.id)
    assert sccs == {0: ["a.py", "b.py"]}


@pytest.mark.asyncio
async def test_membership_upsert_is_idempotent(async_session):
    repo = await insert_repo(async_session)
    membership = {
        "a.py": {"node_type": "file", "scc_id": 1, "scc_size": 2, "symbol_community_id": None},
        "b.py": {"node_type": "file", "scc_id": 1, "scc_size": 2, "symbol_community_id": None},
    }
    await batch_upsert_graph_node_membership(async_session, repo.id, membership)
    await async_session.commit()
    # Re-running updates in place (no duplicate rows).
    membership["a.py"]["scc_size"] = 3
    await batch_upsert_graph_node_membership(async_session, repo.id, membership)
    await async_session.commit()

    sccs = await get_scc_members(async_session, repo.id)
    assert sccs == {1: ["a.py", "b.py"]}
