"""get_execution_flows(entry_point=...) resolves names the way get_symbol does."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.core.persistence.models import GraphNode

_SYMBOL_ID = "src/auth/service.py::AuthService"


@pytest.fixture
async def symbol_node(setup_mcp, session, repo_id):
    session.add(
        GraphNode(
            id="gn-sym",
            repository_id=repo_id,
            node_id=_SYMBOL_ID,
            node_type="symbol",
            name="AuthService",
            kind="class",
            file_path="src/auth/service.py",
            language="python",
            created_at=datetime.now(UTC),
        )
    )
    await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_point", [_SYMBOL_ID, "AuthService", "service.py::AuthService"])
async def test_entry_point_resolves_ids_and_names(symbol_node, entry_point):
    from repowise.server.mcp_server.tool_flows import get_execution_flows

    result = await get_execution_flows(entry_point=entry_point)

    assert "error" not in result
    assert result["flows"][0]["entry_point"] == _SYMBOL_ID


@pytest.mark.asyncio
async def test_unknown_entry_point_still_reports_not_found(symbol_node):
    from repowise.server.mcp_server.tool_flows import get_execution_flows

    result = await get_execution_flows(entry_point="NoSuchThing")

    assert result["error"] == "Symbol not found: 'NoSuchThing'"
