"""External nodes are stored with no community, not as members of community 0.

``graph_nodes.community_id`` is NOT NULL and defaults to 0, which is the largest
real community. An external node left out of the partition must not fall
through to it, or it reappears in that community's member list.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.communities import NO_COMMUNITY, stored_community_id
from repowise.core.persistence.crud.graph import (
    batch_upsert_graph_edges,
    batch_upsert_graph_nodes,
    get_community_members,
    get_cross_community_edges,
)
from tests.unit.persistence.helpers import insert_repo


def test_stored_community_id():
    assignment = {"a.py": 3}
    assert stored_community_id(assignment, "a.py") == 3
    assert stored_community_id(assignment, "external:os") == NO_COMMUNITY
    assert stored_community_id(assignment, "framework:django") == NO_COMMUNITY
    # A symbol is not in the file assignment and keeps the default.
    assert stored_community_id(assignment, "a.py::f") == 0


def _node(node_id: str, community_id: int) -> dict:
    return {"node_id": node_id, "node_type": "file", "community_id": community_id}


@pytest.mark.asyncio
async def test_external_is_not_a_member_or_a_cross_community_target(async_session):
    repo = await insert_repo(async_session)
    await batch_upsert_graph_nodes(
        async_session,
        repo.id,
        [
            _node("a.py", 0),
            _node("b.py", 1),
            _node("external:os", stored_community_id({}, "external:os")),
        ],
    )
    await batch_upsert_graph_edges(
        async_session,
        repo.id,
        [
            {"source_node_id": "a.py", "target_node_id": "b.py", "edge_type": "imports"},
            {"source_node_id": "a.py", "target_node_id": "external:os", "edge_type": "imports"},
        ],
    )
    await async_session.commit()

    members = await get_community_members(async_session, repo.id, 0)
    assert [m.node_id for m in members] == ["a.py"]
    crossing = await get_cross_community_edges(async_session, repo.id, 0)
    assert crossing == [{"target_community_id": 1, "edge_count": 1}]
