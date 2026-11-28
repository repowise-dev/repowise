"""graph_edges.module_form: persisted, rehydrated, and read back as the static-only tier."""

from __future__ import annotations

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.persistence.crud import (
    batch_upsert_graph_edges,
    batch_upsert_graph_nodes,
    get_all_graph_edges,
    get_all_graph_nodes,
)
from repowise.core.persistence.crud.graph import get_scc_members, get_static_only_scc_ids
from repowise.core.persistence.models import GraphNodeMembership
from tests.unit.persistence.helpers import insert_repo


def _edge(source: str, target: str, **marks: object) -> dict:
    return {"source_node_id": source, "target_node_id": target, "edge_type": "imports", **marks}


async def _seed(session) -> str:
    """Two two-file cycles: a<->b only ``import x`` edges, c<->d with one ``from`` edge."""
    repo = await insert_repo(session)
    files = ("a.py", "b.py", "c.py", "d.py")
    await batch_upsert_graph_nodes(
        session,
        repo.id,
        [{"node_id": f, "node_type": "file", "language": "python"} for f in files],
    )
    await batch_upsert_graph_edges(
        session,
        repo.id,
        [
            _edge("a.py", "b.py", module_form=True),
            _edge("b.py", "a.py", module_form=True),
            _edge("c.py", "d.py", module_form=True),
            _edge("d.py", "c.py"),
        ],
    )
    session.add_all(
        GraphNodeMembership(
            repository_id=repo.id, node_id=f, node_type="file", scc_id=scc, scc_size=2
        )
        for scc, members in ((1, ("a.py", "b.py")), (2, ("c.py", "d.py")))
        for f in members
    )
    await session.commit()
    return repo.id


async def test_module_form_round_trips_and_rehydrates(async_session) -> None:
    rid = await _seed(async_session)
    edges = await get_all_graph_edges(async_session, rid)
    marks = {(e["source_node_id"], e["target_node_id"]): e["module_form"] for e in edges}
    assert marks[("a.py", "b.py")] is True
    assert marks[("d.py", "c.py")] is False

    graph = GraphBuilder.from_persisted(await get_all_graph_nodes(async_session, rid), edges)
    assert graph.graph()["a.py"]["b.py"]["module_form"] is True
    assert "module_form" not in graph.graph()["d.py"]["c.py"]
    # Static only is a label, not a removal: both cycles are still found.
    assert len(graph.strongly_connected_components()) == 2


async def test_static_only_scc_ids_read_the_stored_cycle_edges(async_session) -> None:
    rid = await _seed(async_session)
    # A co-change, a self-loop and a dynamic_uses hint: none can close a cycle,
    # so none moves the a<->b cycle out of the static-only tier.
    await batch_upsert_graph_edges(
        async_session,
        rid,
        [
            _edge("b.py", "a.py", edge_type="co_changes"),
            _edge("a.py", "a.py"),
            _edge("a.py", "b.py", edge_type="dynamic_uses"),
        ],
    )
    await async_session.commit()
    sccs = await get_scc_members(async_session, rid)
    assert await get_static_only_scc_ids(async_session, rid, sccs) == {1}
    assert await get_static_only_scc_ids(async_session, rid, {}) == set()
