"""``get_risk`` reads only the graph neighbourhood its cards use.

Loading every node and edge of the repository cost tens of seconds per call on
a large index. The scoped load must still hand each card the same dependents,
the same links and the same node metadata as a whole-graph read would.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from repowise.core.ingestion.models import FILE_DEPENDENCY_EDGE_TYPES
from repowise.core.persistence.models import GraphEdge, GraphNode, Repository
from repowise.server.mcp_server.tool_risk.assessment import _dependency_population
from repowise.server.mcp_server.tool_risk.get_risk import _load_dependency_graph

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)

# (source, target, edge_type): the source depends on the target.
_EDGES = [
    ("b.py", "a.py", "imports"),
    ("b.py", "a.py", "type_use"),
    ("c.py", "b.py", "imports"),
    ("d.py", "c.py", "imports"),  # third hop: outside the walk
    ("e.py", "a.py", "imports"),
    ("a.py", "z.py", "imports"),
    ("a.py", "q.py", "co_changes"),  # not a dependency
    ("a.py", "a.py::F", "defines"),  # containment, not a dependency
    ("y.py", "x.py", "imports"),  # unrelated island
]


@pytest.fixture
async def graph_repo(factory) -> str:
    async with factory() as s:
        s.add(
            Repository(
                id="r",
                name="r",
                url="",
                local_path="",
                default_branch="main",
                settings_json="{}",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        node_ids = sorted({n for edge in _EDGES for n in edge[:2]})
        for i, node_id in enumerate(node_ids):
            s.add(
                GraphNode(
                    id=f"n{i}",
                    repository_id="r",
                    node_id=node_id,
                    node_type="symbol" if "::" in node_id else "file",
                    language="python",
                    pagerank=i / 10,
                    is_entry_point=node_id == "c.py",
                    created_at=_NOW,
                )
            )
        for i, (src, tgt, kind) in enumerate(_EDGES):
            s.add(
                GraphEdge(
                    id=f"e{i}",
                    repository_id="r",
                    source_node_id=src,
                    target_node_id=tgt,
                    imported_names_json="[]",
                    edge_type=kind,
                    created_at=_NOW,
                )
            )
        await s.commit()
    return "r"


async def _whole_graph(session, repo_id):
    """The whole-repository read the scoped load replaced, as the oracle."""
    nodes = (
        (await session.execute(select(GraphNode).where(GraphNode.repository_id == repo_id)))
        .scalars()
        .all()
    )
    node_meta = {n.node_id: n for n in nodes}
    files = {n.node_id for n in nodes if n.node_type == "file"}
    edges = (
        (
            await session.execute(
                select(GraphEdge).where(
                    GraphEdge.repository_id == repo_id,
                    GraphEdge.edge_type.in_(FILE_DEPENDENCY_EDGE_TYPES),
                )
            )
        )
        .scalars()
        .all()
    )
    links: dict = {}
    reverse: dict = {}
    for e in edges:
        if e.source_node_id in files and e.target_node_id in files:
            kind = str(e.edge_type)
            links.setdefault(e.source_node_id, {}).setdefault(e.target_node_id, set()).add(kind)
            links.setdefault(e.target_node_id, {}).setdefault(e.source_node_id, set()).add(kind)
            reverse.setdefault(e.target_node_id, {}).setdefault(e.source_node_id, set()).add(kind)
    return node_meta, links, reverse


@pytest.mark.asyncio
@pytest.mark.parametrize("roots", [{"a.py"}, {"b.py"}, {"a.py", "c.py"}, {"a.py::F"}, {"nope.py"}])
async def test_scoped_load_matches_whole_graph_evidence(factory, graph_repo, roots):
    async with factory() as s:
        full_meta, full_links, full_reverse = await _whole_graph(s, graph_repo)
        scoped = await _load_dependency_graph(s, graph_repo, roots)

    for root in roots:
        assert _dependency_population(root, scoped.reverse_deps, scoped.node_meta) == (
            _dependency_population(root, full_reverse, full_meta)
        )
        assert scoped.import_links.get(root, {}) == full_links.get(root, {})
        assert (root in scoped.node_meta) is (root in full_meta)


@pytest.mark.asyncio
async def test_scoped_load_stops_at_the_second_hop(factory, graph_repo):
    async with factory() as s:
        scoped = await _load_dependency_graph(s, graph_repo, {"a.py"})

    # The root, its dependents within two hops, and what the root imports.
    assert set(scoped.node_meta) == {"a.py", "b.py", "c.py", "e.py", "z.py"}
    assert scoped.dep_counts["a.py"] == 2
