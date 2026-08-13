"""``get_dependency_path`` must walk dependency edges only.

The tool loaded every ``graph_edges`` row for the repository into a NetworkX
graph and reported the shortest path through it. The table also carries the
temporal ``co_changes`` relation and file-to-symbol containment, so a
co-change edge was a free hop: the tool would answer "these two files are
connected" and name a path that no import, call or type reference creates.

Same root cause as the ``get_risk`` filter in ``test_risk_dependency_edges``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode

_ORPHAN = "src/reports/exporter.py"
_TARGET = "src/db/models.py"
_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


async def _add_co_change_only_neighbour(factory, repo_id: str) -> None:
    """A file with no code link to the graph, joined only by co-change."""
    async with factory() as s:
        s.add(
            GraphNode(
                id="gn-orphan",
                repository_id=repo_id,
                node_id=_ORPHAN,
                node_type="file",
                language="python",
                symbol_count=1,
                is_entry_point=False,
                pagerank=0.05,
                betweenness=0.0,
                community_id=3,
                created_at=_NOW,
            )
        )
        s.add(
            GraphEdge(
                id="ge-cochange-path",
                repository_id=repo_id,
                source_node_id=_ORPHAN,
                target_node_id=_TARGET,
                imported_names_json="[]",
                edge_type="co_changes",
                created_at=_NOW,
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_co_change_edge_is_not_a_dependency_path(setup_mcp, factory):
    from repowise.server.mcp_server import get_dependency_path

    await _add_co_change_only_neighbour(factory, setup_mcp)

    result = await get_dependency_path(_ORPHAN, _TARGET)

    assert result.get("path") in (None, []), (
        f"co_changes edge was walked as a dependency: {result.get('path')}"
    )
    # Shortest-path must be what rejected the hop. If the node had instead
    # fallen out of the graph, this would pass while silently killing the
    # no-path fallbacks below — which is exactly how an earlier version of
    # this test passed for the wrong reason.
    assert "not found in graph" not in (result.get("explanation") or "")


@pytest.mark.asyncio
async def test_a_node_with_only_temporal_edges_still_reaches_the_fallbacks(
    setup_mcp, factory
):
    """Filtering edges must not delete nodes.

    The graph is built from edge endpoints, so a file whose only edges were
    temporal used to vanish and return "not found in graph" — skipping the
    no-path branch that carries ``visual_context`` and the ``co_change_signal``
    written for precisely this case.
    """
    from repowise.server.mcp_server import get_dependency_path

    await _add_co_change_only_neighbour(factory, setup_mcp)

    result = await get_dependency_path(_ORPHAN, _TARGET)

    assert "not found in graph" not in (result.get("explanation") or "")
    assert result.get("visual_context") is not None


@pytest.mark.asyncio
async def test_a_path_through_the_symbol_layer_still_resolves(setup_mcp, factory):
    """``defines`` is the only bridge from a file to its symbols.

    Nothing points from a symbol back to a file, so excluding containment can
    never suppress a false file-to-file path. It only deletes the real
    file → symbol → symbol answer to "which file reaches this function".
    """
    from repowise.server.mcp_server import get_dependency_path

    async with factory() as s:
        s.add(
            GraphNode(
                id="gn-sym",
                repository_id=setup_mcp,
                node_id="src/db/models.py::User",
                node_type="symbol",
                language="python",
                symbol_count=0,
                is_entry_point=False,
                pagerank=0.1,
                betweenness=0.0,
                community_id=2,
                created_at=_NOW,
            )
        )
        s.add(
            GraphEdge(
                id="ge-defines-path",
                repository_id=setup_mcp,
                source_node_id=_TARGET,
                target_node_id="src/db/models.py::User",
                imported_names_json="[]",
                edge_type="defines",
                created_at=_NOW,
            )
        )
        await s.commit()

    result = await get_dependency_path(_TARGET, "src/db/models.py::User")

    assert [hop["node"] for hop in result.get("path") or []] == [
        _TARGET,
        "src/db/models.py::User",
    ]


@pytest.mark.asyncio
async def test_real_import_path_still_resolves(setup_mcp, factory):
    """The filter must not empty the graph — the seeded import chain stands."""
    from repowise.server.mcp_server import get_dependency_path

    await _add_co_change_only_neighbour(factory, setup_mcp)

    result = await get_dependency_path("src/auth/middleware.py", _TARGET)

    assert [hop["node"] for hop in result.get("path") or []] == [
        "src/auth/middleware.py",
        "src/auth/service.py",
        _TARGET,
    ]
