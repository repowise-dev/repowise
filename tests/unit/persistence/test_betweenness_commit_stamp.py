"""``graph_nodes.betweenness_commit``: scored, unscored, and unknown.

Exact betweenness is reused across small structural changes, so a node added
since the last scoring holds the column's 0.0 default without having been
measured. The stamp is what tells those apart, and it has three states, not
two: a commit, NULL for "this scoring did not contain the node", and — the one
that is easy to get wrong — *absent from the payload* when the builder cannot
say, so a row that already carries a good stamp keeps it.
"""

from __future__ import annotations

import networkx as nx
import pytest
from sqlalchemy import select

from repowise.core.ingestion.graph._centrality_cache import BetweennessScoring
from repowise.core.persistence.models import GraphNode
from repowise.core.pipeline.persist import persist_graph_nodes
from tests.unit.persistence.helpers import insert_repo


class _Builder:
    """Minimal stand-in for the parts of GraphBuilder persist_graph_nodes reads."""

    def __init__(self, graph, sym_bc, scoring):
        self._graph = graph
        self._sym_bc = sym_bc
        self._scoring = scoring

    def graph(self):
        return self._graph

    def pagerank(self):
        return {}

    def betweenness_centrality(self):
        return {}

    def symbol_pagerank(self):
        return {}

    def symbol_betweenness_centrality(self):
        return self._sym_bc

    def community_detection(self):
        return {}

    def symbol_communities(self):
        return {}

    def community_info(self):
        return {}

    def betweenness_scoring(self, kind):
        return self._scoring.get(kind)


def _graph(*symbol_ids):
    g = nx.DiGraph()
    for sid in symbol_ids:
        g.add_node(sid, node_type="symbol", language="python")
    return g


async def _stamps(session, repo_id):
    rows = (await session.execute(select(GraphNode).where(GraphNode.repository_id == repo_id))).scalars()
    return {r.node_id: r.betweenness_commit for r in rows}


@pytest.fixture
async def repo_id(async_session):
    return (await insert_repo(async_session)).id


async def test_scored_nodes_stamped_and_new_node_marked_unscored(async_session, repo_id):
    """A reused scoring stamps its own commit, and the symbol it never saw NULL."""
    graph = _graph("a.py::A", "a.py::B", "a.py::New")
    sym_bc = {"a.py::A": 0.5, "a.py::B": 0.0}  # "New" appeared after the scoring
    scoring = {"symbol": BetweennessScoring(sym_bc, "c0ffee", churn=2)}

    await persist_graph_nodes(async_session, repo_id, _Builder(graph, sym_bc, scoring))
    await async_session.commit()

    stamps = await _stamps(async_session, repo_id)
    assert stamps["a.py::A"] == "c0ffee"
    # Genuinely scored at 0.0 is not the same as never scored.
    assert stamps["a.py::B"] == "c0ffee"
    assert stamps["a.py::New"] is None


async def test_unknown_provenance_preserves_the_existing_stamp(async_session, repo_id):
    """A builder rehydrated from SQL serves real values it did not compute.

    Stamping those NULL would report an entire indexed repository as unscored,
    so the field must be omitted and the stored stamp left standing.
    """
    graph = _graph("a.py::A")
    sym_bc = {"a.py::A": 0.5}
    await persist_graph_nodes(
        async_session, repo_id, _Builder(graph, sym_bc, {"symbol": BetweennessScoring(sym_bc, "c0ffee", 0)})
    )
    await async_session.commit()
    assert (await _stamps(async_session, repo_id))["a.py::A"] == "c0ffee"

    # Same values, no provenance — the rehydrated case.
    await persist_graph_nodes(async_session, repo_id, _Builder(graph, sym_bc, {}))
    await async_session.commit()
    assert (await _stamps(async_session, repo_id))["a.py::A"] == "c0ffee"


async def test_builder_without_the_accessor_is_tolerated(async_session, repo_id):
    """Duck-typed stand-ins predate ``betweenness_scoring`` and must not break."""
    graph = _graph("a.py::A")
    builder = _Builder(graph, {"a.py::A": 0.5}, {})
    del builder.__class__.betweenness_scoring  # simulate an older stand-in
    try:
        await persist_graph_nodes(async_session, repo_id, builder)
        await async_session.commit()
        assert (await _stamps(async_session, repo_id))["a.py::A"] is None
    finally:
        _Builder.betweenness_scoring = lambda self, kind: self._scoring.get(kind)
