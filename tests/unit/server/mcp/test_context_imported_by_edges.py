"""``get_context`` must not serve non-reference edges under ``imported_by``.

Two scans in ``tool_context/targets.py`` collected every inbound ``graph_edges``
row for a target and published the sources as ``imported_by`` (file target) and
``used_by`` (symbol target). Both keys name a code reference, so a co-change
partner arriving there tells the agent that a file imports another when nothing
in the source says so.

This is the same defect as the ``get_risk`` filter, in a default-on tool.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.core.persistence.models import GraphEdge

_TARGET = "src/auth/service.py"
_PARTNER = "src/reports/exporter.py"
_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


async def _add_inbound_co_change(factory, repo_id: str) -> None:
    async with factory() as s:
        s.add(
            GraphEdge(
                id="ge-cochange-context",
                repository_id=repo_id,
                source_node_id=_PARTNER,
                target_node_id=_TARGET,
                imported_names_json="[]",
                edge_type="co_changes",
                created_at=_NOW,
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_co_change_partner_is_not_an_importer(setup_mcp, factory):
    from repowise.server.mcp_server import get_context

    await _add_inbound_co_change(factory, setup_mcp)

    result = await get_context(targets=[_TARGET], compact=False)
    imported_by = result["targets"][_TARGET]["docs"]["imported_by"]

    assert _PARTNER not in imported_by, "a co-change partner was published as an importer"
    # The real importer survives, so the filter has not blanked the field.
    assert "src/auth/middleware.py" in imported_by
