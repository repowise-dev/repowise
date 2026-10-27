"""Behavioral tests for the single-existence-query batch upsert.

The keyed implementation replaced a per-item SELECT loop; these tests pin
the semantics the callers rely on: insert-then-update convergence,
within-batch duplicate handling, per-repo isolation, and empty input.
"""

from __future__ import annotations

from sqlalchemy import select

from repowise.core.persistence.crud import (
    batch_upsert_graph_edges,
    batch_upsert_graph_nodes,
    upsert_git_metadata_bulk,
)
from repowise.core.persistence.models import GitMetadata, GraphEdge, GraphNode
from tests.unit.persistence.helpers import insert_repo


def _node(node_id: str, pagerank: float = 0.0) -> dict:
    return {
        "node_id": node_id,
        "node_type": "file",
        "language": "python",
        "symbol_count": 1,
        "pagerank": pagerank,
        "betweenness": 0.0,
        "community_id": 0,
    }


async def _node_rows(session, repo_id: str) -> dict[str, GraphNode]:
    rows = (
        (await session.execute(select(GraphNode).where(GraphNode.repository_id == repo_id)))
        .scalars()
        .all()
    )
    return {r.node_id: r for r in rows}


async def test_insert_then_update_converges(async_session):
    repo = await insert_repo(async_session)
    await batch_upsert_graph_nodes(async_session, repo.id, [_node("a.py"), _node("b.py")])
    await async_session.commit()

    await batch_upsert_graph_nodes(
        async_session, repo.id, [_node("a.py", pagerank=0.5), _node("c.py")]
    )
    await async_session.commit()

    rows = await _node_rows(async_session, repo.id)
    assert set(rows) == {"a.py", "b.py", "c.py"}
    assert rows["a.py"].pagerank == 0.5
    # Update must not duplicate the row.
    ids = [r.id for r in rows.values()]
    assert len(ids) == len(set(ids))


async def test_within_batch_duplicate_keys_last_wins_single_row(async_session):
    repo = await insert_repo(async_session)
    await batch_upsert_graph_nodes(
        async_session, repo.id, [_node("dup.py", 0.1), _node("dup.py", 0.9)]
    )
    await async_session.commit()

    rows = await _node_rows(async_session, repo.id)
    assert set(rows) == {"dup.py"}
    assert rows["dup.py"].pagerank == 0.9


async def test_repos_are_isolated(async_session):
    repo_a = await insert_repo(async_session)
    repo_b = await insert_repo(async_session, name="other", local_path="/other")
    await batch_upsert_graph_nodes(async_session, repo_a.id, [_node("shared.py", 0.1)])
    await batch_upsert_graph_nodes(async_session, repo_b.id, [_node("shared.py", 0.7)])
    await async_session.commit()

    rows_a = await _node_rows(async_session, repo_a.id)
    rows_b = await _node_rows(async_session, repo_b.id)
    assert rows_a["shared.py"].pagerank == 0.1
    assert rows_b["shared.py"].pagerank == 0.7


async def test_empty_input_is_a_noop(async_session):
    repo = await insert_repo(async_session)
    await batch_upsert_graph_nodes(async_session, repo.id, [])
    await upsert_git_metadata_bulk(async_session, repo.id, [])
    await async_session.commit()
    assert await _node_rows(async_session, repo.id) == {}


async def test_edges_composite_key(async_session):
    repo = await insert_repo(async_session)
    edge = {
        "source_node_id": "a.py",
        "target_node_id": "b.py",
        "edge_type": "imports",
        "imported_names_json": "[]",
        "confidence": 0.5,
    }
    await batch_upsert_graph_edges(async_session, repo.id, [edge])
    # Same pair, different edge_type -> second row; same triple -> update.
    await batch_upsert_graph_edges(
        async_session,
        repo.id,
        [
            {**edge, "edge_type": "calls"},
            {**edge, "confidence": 1.0},
        ],
    )
    await async_session.commit()

    rows = (
        (await session_exec(async_session, repo.id)).scalars().all()
    )
    by_type = {r.edge_type: r for r in rows}
    assert set(by_type) == {"imports", "calls"}
    assert by_type["imports"].confidence == 1.0


async def session_exec(session, repo_id):
    return await session.execute(select(GraphEdge).where(GraphEdge.repository_id == repo_id))


async def test_git_metadata_update_path(async_session):
    repo = await insert_repo(async_session)
    await upsert_git_metadata_bulk(
        async_session, repo.id, [{"file_path": "a.py", "commit_count_30d": 1}]
    )
    await upsert_git_metadata_bulk(
        async_session, repo.id, [{"file_path": "a.py", "commit_count_30d": 7}]
    )
    await async_session.commit()

    rows = (
        (
            await async_session.execute(
                select(GitMetadata).where(GitMetadata.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].commit_count_30d == 7


def _edge(source: str, confidence: float = 0.5) -> dict:
    return {
        "source_node_id": source,
        "target_node_id": "t.py",
        "edge_type": "imports",
        "confidence": confidence,
    }


async def test_duplicate_key_across_a_flush_boundary_updates_one_row(async_session):
    """A key repeated after its insert was flushed still lands one row, last wins."""
    from repowise.core.persistence.crud._shared import _BATCH_SIZE

    repo = await insert_repo(async_session)
    edges = [_edge(f"s{i}.py") for i in range(_BATCH_SIZE + 10)]
    edges.append(_edge("s0.py", confidence=0.9))
    await batch_upsert_graph_edges(async_session, repo.id, edges)
    await async_session.commit()

    rows = (await session_exec(async_session, repo.id)).scalars().all()
    assert len(rows) == _BATCH_SIZE + 10
    assert next(r for r in rows if r.source_node_id == "s0.py").confidence == 0.9


async def test_inserted_rows_are_never_tracked_by_the_session(async_session):
    """New rows go out as a bulk INSERT, not as session-held ORM objects.

    Holding every inserted object pinned a whole table in memory at once; on a
    1.8M-edge graph that was ~5 GiB on top of the run.
    """
    from repowise.core.persistence.crud._shared import _BATCH_SIZE

    repo = await insert_repo(async_session)
    total = _BATCH_SIZE * 3
    await batch_upsert_graph_edges(async_session, repo.id, [_edge(f"s{i}.py") for i in range(total)])

    assert not [o for o in async_session.identity_map.values() if isinstance(o, GraphEdge)]
    assert len((await session_exec(async_session, repo.id)).scalars().all()) == total


async def test_bulk_insert_writes_what_a_flush_would(async_session):
    """Unset and None columns get their defaults; other Nones stay NULL."""
    repo = await insert_repo(async_session)
    await batch_upsert_graph_nodes(
        async_session,
        repo.id,
        [{"node_id": "a.py", "language": None, "pagerank": None}, {"node_id": "b.py", "name": "B"}],
    )
    await async_session.commit()

    rows = await _node_rows(async_session, repo.id)
    a, b = rows["a.py"], rows["b.py"]
    assert (a.node_type, a.language, a.pagerank, a.symbol_count) == ("file", "", 0.0, 0)
    assert a.is_reachability_root is False and a.created_at is not None
    assert a.name is None and b.name == "B"


async def test_within_batch_duplicate_replays_the_update(async_session):
    """A key repeated before its row is written merges like an update would."""
    repo = await insert_repo(async_session)
    await batch_upsert_graph_edges(
        async_session, repo.id, [_edge("a.py", confidence=0.9), _edge("a.py", confidence=0.4)]
    )
    await async_session.commit()

    rows = (await session_exec(async_session, repo.id)).scalars().all()
    assert len(rows) == 1
    # The edge update keeps the max confidence, as it did on the pending object.
    assert rows[0].confidence == 0.9


async def test_unknown_column_is_rejected(async_session):
    import pytest

    repo = await insert_repo(async_session)
    with pytest.raises(TypeError):
        await batch_upsert_graph_nodes(async_session, repo.id, [{"node_id": "a.py", "bogus": 1}])
