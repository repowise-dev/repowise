from types import SimpleNamespace

import pytest

from repowise.server.mcp_server._graph_utils import resolve_trace_communities


@pytest.mark.asyncio
async def test_external_trace_node_is_not_a_visited_community():
    cache = {
        "a.py": SimpleNamespace(community_id=0),
        "external:os": SimpleNamespace(community_id=-1),
    }
    visited, crosses = await resolve_trace_communities(
        None, "repo", ["a.py", "external:os"], cache
    )
    assert visited == [0]
    assert crosses is False