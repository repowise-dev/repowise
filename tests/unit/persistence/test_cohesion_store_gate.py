"""A stored edge counts as a cohesion stamp only when its hint is a cohesion hint.

Other passes stamp ``hint_source`` too (the pytest conftest convention edge),
so a store holding only those rows still predates cohesion edges and must be
reconciled once.
"""

from __future__ import annotations

from types import SimpleNamespace

import networkx as nx

from repowise.core.ingestion.cohesion import SAME_PACKAGE_HINT
from repowise.core.ingestion.framework_edges.pytest_edges import CONFTEST_HINT
from repowise.core.persistence.models import GraphEdge
from repowise.core.pipeline.persist import _edges_predate_cohesion
from tests.unit.persistence.helpers import insert_repo


def _builder_with_a_cohesion_edge() -> SimpleNamespace:
    graph = nx.DiGraph()
    graph.add_edge("pkg/a.go", "pkg/b.go", hint_source=SAME_PACKAGE_HINT)
    return SimpleNamespace(graph=lambda: graph)


async def _store_edge(session, repo_id: str, hint: str) -> None:
    session.add(
        GraphEdge(
            repository_id=repo_id,
            source_node_id="tests/test_a.py",
            target_node_id="tests/conftest.py",
            hint_source=hint,
        )
    )
    await session.commit()


async def test_a_conftest_hint_is_not_a_cohesion_stamp(async_session):
    repo = await insert_repo(async_session)
    await _store_edge(async_session, repo.id, CONFTEST_HINT)
    assert await _edges_predate_cohesion(async_session, repo.id, _builder_with_a_cohesion_edge())


async def test_a_stored_cohesion_hint_ends_the_reconcile(async_session):
    repo = await insert_repo(async_session)
    await _store_edge(async_session, repo.id, SAME_PACKAGE_HINT)
    assert not await _edges_predate_cohesion(
        async_session, repo.id, _builder_with_a_cohesion_edge()
    )
