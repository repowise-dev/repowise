"""The graph snapshot upserts skip rows that already say what the payload says.

``persist_graph_nodes`` hands ``batch_upsert_graph_nodes`` /
``batch_upsert_graph_metrics`` / ``batch_upsert_graph_node_membership`` a full
snapshot of the repo's graph on every update, so a one-file change arrives as
tens of thousands of rewrites of rows that did not move. The gate skips those
before they are hydrated as ORM objects.

A gate that skips a write it should have made is indistinguishable from a
correct no-op at the call site, so the two-database parity run at the bottom
compares the gated path against the same path with the gate removed, and the
one deliberate divergence (the centrality tolerance) is kept out of that
scenario and tested on its own.

Read the green here for what it is. Most of these are no-regression guards: they
pass against a build with no gate at all, which is the point, since the gate is
supposed to be invisible. The ones that actually fail when the feature is
removed or weakened, and are therefore the evidence it works, are:

- ``test_subtolerance_centrality_drift_is_not_written`` (gate present at all),
  paired with ``..._is_the_gate_and_not_the_storage`` (the same move lands
  without the gate, so the first is not testing a lossy float column),
- ``test_repeated_subtolerance_drift_cannot_accumulate`` (both bounds),
- ``test_unknown_payload_field_disables_the_skip`` (a field the gate cannot
  compare must never be skipped),
- ``test_duplicate_keys_keep_last_write_wins`` (the duplicate-key opt-out).
"""

from __future__ import annotations

import json

from sqlalchemy import select

from repowise.core.persistence.crud.graph import (
    _CENTRALITY_ATOL,
    batch_upsert_graph_node_membership,
    batch_upsert_graph_nodes,
)
from repowise.core.persistence.models import GraphMetric, GraphNode, GraphNodeMembership
from tests.unit.persistence.helpers import insert_repo


def file_node(node_id: str, **over) -> dict:
    base = dict(
        node_id=node_id,
        node_type="file",
        language="python",
        symbol_count=3,
        has_error=False,
        is_test=False,
        is_entry_point=False,
        pagerank=0.25,
        betweenness=0.5,
        community_id=1,
        community_meta_json=json.dumps({"label": "core", "cohesion": 0.5}),
    )
    base.update(over)
    return base


def symbol_node(node_id: str, **over) -> dict:
    base = dict(
        node_id=node_id,
        node_type="symbol",
        language="python",
        symbol_count=0,
        has_error=False,
        is_test=False,
        is_entry_point=False,
        pagerank=0.01,
        betweenness=0.0,
        community_id=0,
        community_meta_json=json.dumps({"symbol_community_id": 7}),
        kind="function",
        name="f",
        qualified_name="m.f",
        file_path="m.py",
        start_line=1,
        end_line=9,
        visibility="public",
        signature="def f()",
        parent_symbol_id=None,
    )
    base.update(over)
    return base


async def _nodes(session, rid) -> dict[str, dict]:
    rows = (
        (await session.execute(select(GraphNode).where(GraphNode.repository_id == rid)))
        .scalars()
        .all()
    )
    return {
        r.node_id: {
            c.name: getattr(r, c.name)
            for c in GraphNode.__table__.columns
            if c.name not in ("id", "created_at")
        }
        for r in rows
    }


async def _metrics(session, rid) -> dict[str, dict]:
    rows = (
        (await session.execute(select(GraphMetric).where(GraphMetric.repository_id == rid)))
        .scalars()
        .all()
    )
    return {
        r.node_id: {
            c.name: getattr(r, c.name)
            for c in GraphMetric.__table__.columns
            if c.name not in ("id", "created_at")
        }
        for r in rows
    }


async def _membership(session, rid) -> dict[str, dict]:
    rows = (
        (
            await session.execute(
                select(GraphNodeMembership).where(GraphNodeMembership.repository_id == rid)
            )
        )
        .scalars()
        .all()
    )
    return {
        r.node_id: {
            c.name: getattr(r, c.name)
            for c in GraphNodeMembership.__table__.columns
            if c.name not in ("id", "created_at")
        }
        for r in rows
    }


# ---------------------------------------------------------------------------
# The intentional delta: what "unchanged" means for a centrality float
# ---------------------------------------------------------------------------


async def test_subtolerance_centrality_drift_is_not_written(async_session):
    """A move smaller than the tolerance leaves the stored value alone.

    This is the whole point of an absolute tolerance rather than exact
    equality: the kernels are not bit-stable across processes, so exact
    equality would rewrite every file node on a repo where nothing changed.
    """
    repo = await insert_repo(async_session)
    await batch_upsert_graph_nodes(async_session, repo.id, [file_node("a.py")])
    await async_session.commit()

    await batch_upsert_graph_nodes(
        async_session, repo.id, [file_node("a.py", pagerank=0.25 + _CENTRALITY_ATOL / 10)]
    )
    await async_session.commit()

    assert (await _nodes(async_session, repo.id))["a.py"]["pagerank"] == 0.25


async def test_subtolerance_drift_is_the_gate_and_not_the_storage(async_session):
    """The same sub-tolerance move, with the gate removed, does land.

    Without this the test above would pass just as happily against a build
    that has no gate at all, or against a float column that silently rounds.
    """
    from repowise.core.persistence.crud import graph as graph_crud

    repo = await insert_repo(async_session)
    original = graph_crud._batch_upsert_keyed

    async def no_gate(*args, **kwargs):
        kwargs.pop("gate", None)
        return await original(*args, **kwargs)

    graph_crud._batch_upsert_keyed = no_gate
    try:
        await graph_crud.batch_upsert_graph_nodes(async_session, repo.id, [file_node("a.py")])
        await async_session.commit()
        moved = 0.25 + _CENTRALITY_ATOL / 10
        await graph_crud.batch_upsert_graph_nodes(
            async_session, repo.id, [file_node("a.py", pagerank=moved)]
        )
        await async_session.commit()
    finally:
        graph_crud._batch_upsert_keyed = original

    assert (await _nodes(async_session, repo.id))["a.py"]["pagerank"] == moved


async def test_supratolerance_centrality_move_is_written(async_session):
    repo = await insert_repo(async_session)
    await batch_upsert_graph_nodes(async_session, repo.id, [file_node("a.py")])
    await async_session.commit()

    moved = 0.25 + _CENTRALITY_ATOL * 100
    await batch_upsert_graph_nodes(async_session, repo.id, [file_node("a.py", pagerank=moved)])
    await async_session.commit()

    assert (await _nodes(async_session, repo.id))["a.py"]["pagerank"] == moved


async def test_repeated_subtolerance_drift_cannot_accumulate(async_session):
    """A drift that creeps by a quarter of the tolerance per run still lands.

    Every comparison is against the *stored* value, never the previous run's
    fresh value, so the distance from storage grows until it crosses the
    tolerance and forces a write. Asserting only the final value would pass
    against a build with no gate, which writes every step and therefore also
    ends up correct: the per-step trace below is what separates them. Storage
    holds still for the first four steps and jumps on the fifth.
    """
    repo = await insert_repo(async_session)
    await batch_upsert_graph_nodes(async_session, repo.id, [file_node("a.py")])
    await async_session.commit()

    step = _CENTRALITY_ATOL / 4
    trace = []
    for i in range(1, 11):
        await batch_upsert_graph_nodes(
            async_session, repo.id, [file_node("a.py", pagerank=0.25 + step * i)]
        )
        await async_session.commit()
        trace.append((await _nodes(async_session, repo.id))["a.py"]["pagerank"])

    assert trace[:4] == [0.25] * 4, "a sub-tolerance move was written"
    assert trace[4] == 0.25 + step * 5, "the crossing step was not written"
    # Never further from the truth than the tolerance, at any point.
    assert all(abs(trace[i] - (0.25 + step * (i + 1))) <= _CENTRALITY_ATOL for i in range(10))


# ---------------------------------------------------------------------------
# The gate must never be the reason a write goes missing
# ---------------------------------------------------------------------------


async def test_non_float_change_is_always_written(async_session):
    """Integers and strings get no tolerance: a community relabel lands."""
    repo = await insert_repo(async_session)
    await batch_upsert_graph_nodes(async_session, repo.id, [file_node("a.py")])
    await async_session.commit()

    await batch_upsert_graph_nodes(
        async_session,
        repo.id,
        [file_node("a.py", community_id=2, community_meta_json='{"label": "cli"}')],
    )
    await async_session.commit()

    row = (await _nodes(async_session, repo.id))["a.py"]
    assert row["community_id"] == 2
    assert row["community_meta_json"] == '{"label": "cli"}'


def test_node_gate_columns_account_for_every_writable_column():
    """``_NODE_FIELDS`` is a hand-kept mirror of the model's columns.

    A new ``GraphNode`` column that the node payload starts carrying, without a
    matching entry here, turns the gate off for every node — a silent loss of
    the whole win rather than of a write. This forces the decision at the point
    the column is added.
    """
    from repowise.core.persistence.crud.graph import _NODE_FIELDS

    managed_elsewhere = {
        "id",
        "repository_id",
        "created_at",  # never written by _update_graph_node
        "node_id",  # the match key
        "external_system_id",  # owned by the external-systems linker
    }
    assert set(_NODE_FIELDS) | managed_elsewhere == {
        c.name for c in GraphNode.__table__.columns
    }


async def test_unknown_payload_field_disables_the_skip(async_session):
    """A field the gate cannot compare must cost a write, never lose one.

    Guards the failure mode where someone adds a column to the node payload
    and the gate quietly stops persisting it because everything else matched.
    """
    repo = await insert_repo(async_session)
    await batch_upsert_graph_nodes(async_session, repo.id, [file_node("a.py")])
    await async_session.commit()

    await batch_upsert_graph_nodes(
        async_session, repo.id, [file_node("a.py", external_system_id=42)]
    )
    await async_session.commit()

    assert (await _nodes(async_session, repo.id))["a.py"]["external_system_id"] == 42


async def test_duplicate_keys_keep_last_write_wins(async_session):
    """Two items with the same key: the gate steps aside and the legacy
    last-write-wins outcome is preserved.

    The ordering matters. The second item is the one that matches the stored
    row, so a gate that ran per item would skip it, apply only the first, and
    invert the outcome. An ordering where both items differ from storage would
    pass with the duplicate-key check deleted.
    """
    repo = await insert_repo(async_session)
    await batch_upsert_graph_nodes(async_session, repo.id, [file_node("a.py", community_id=1)])
    await async_session.commit()

    await batch_upsert_graph_nodes(
        async_session,
        repo.id,
        [file_node("a.py", community_id=5), file_node("a.py", community_id=1)],
    )
    await async_session.commit()

    assert (await _nodes(async_session, repo.id))["a.py"]["community_id"] == 1


async def test_new_rows_still_insert_when_everything_else_is_unchanged(async_session):
    repo = await insert_repo(async_session)
    await batch_upsert_graph_nodes(async_session, repo.id, [file_node("a.py")])
    await async_session.commit()

    await batch_upsert_graph_nodes(
        async_session, repo.id, [file_node("a.py"), file_node("b.py")]
    )
    await async_session.commit()

    assert set(await _nodes(async_session, repo.id)) == {"a.py", "b.py"}


async def test_sparse_and_dense_change_sets_land_the_same_rows(async_session):
    """The gate reloads survivors by key when few rows moved and falls back to
    one unfiltered scan when most did. Both branches must write the same thing."""
    repo = await insert_repo(async_session)
    await batch_upsert_graph_nodes(
        async_session, repo.id, [file_node(f"m{i}.py", community_id=0) for i in range(10)]
    )
    await async_session.commit()

    # sparse: one row of ten moves
    await batch_upsert_graph_nodes(
        async_session,
        repo.id,
        [file_node(f"m{i}.py", community_id=1 if i == 3 else 0) for i in range(10)],
    )
    await async_session.commit()
    rows = await _nodes(async_session, repo.id)
    assert [rows[f"m{i}.py"]["community_id"] for i in range(10)] == [0, 0, 0, 1, 0, 0, 0, 0, 0, 0]

    # dense: nine of ten move
    await batch_upsert_graph_nodes(
        async_session,
        repo.id,
        [file_node(f"m{i}.py", community_id=0 if i == 3 else 7) for i in range(10)],
    )
    await async_session.commit()
    rows = await _nodes(async_session, repo.id)
    assert [rows[f"m{i}.py"]["community_id"] for i in range(10)] == [7, 7, 7, 0, 7, 7, 7, 7, 7, 7]


async def test_membership_prune_still_runs_when_nothing_else_changed(async_session):
    """The absent-node delete is upstream of the gate and must survive it."""
    repo = await insert_repo(async_session)
    snapshot = {
        "a.py": {"node_type": "file", "scc_id": 0, "scc_size": 2, "symbol_community_id": None},
        "b.py": {"node_type": "file", "scc_id": 0, "scc_size": 2, "symbol_community_id": None},
    }
    await batch_upsert_graph_node_membership(async_session, repo.id, snapshot)
    await async_session.commit()

    await batch_upsert_graph_node_membership(
        async_session, repo.id, {"a.py": snapshot["a.py"]}
    )
    await async_session.commit()

    assert set(await _membership(async_session, repo.id)) == {"a.py"}


async def test_empty_snapshot_prunes_everything(async_session):
    repo = await insert_repo(async_session)
    await batch_upsert_graph_node_membership(
        async_session,
        repo.id,
        {"a.py": {"node_type": "file", "scc_id": 1, "scc_size": 2, "symbol_community_id": None}},
    )
    await async_session.commit()

    await batch_upsert_graph_node_membership(async_session, repo.id, {})
    await async_session.commit()

    assert await _membership(async_session, repo.id) == {}


async def test_gate_is_scoped_to_the_repository(async_session):
    """Two repos can hold the same node_id; the gate must not read one repo's
    values as the other's."""
    a = await insert_repo(async_session, name="a", local_path="/tmp/a")
    b = await insert_repo(async_session, name="b", local_path="/tmp/b")
    await batch_upsert_graph_nodes(async_session, a.id, [file_node("x.py", community_id=1)])
    await async_session.commit()
    # Different values, so a gate reading repo A's row for repo B fails on the
    # value rather than only on the row being absent.
    await batch_upsert_graph_nodes(async_session, b.id, [file_node("x.py", community_id=2)])
    await async_session.commit()
    await batch_upsert_graph_nodes(async_session, b.id, [file_node("x.py", community_id=1)])
    await async_session.commit()

    assert (await _nodes(async_session, b.id))["x.py"]["community_id"] == 1
    assert (await _nodes(async_session, a.id))["x.py"]["community_id"] == 1


# ---------------------------------------------------------------------------
# Row-for-row parity against the ungated path
# ---------------------------------------------------------------------------


async def _fresh_db():
    from repowise.core.persistence.database import (
        create_engine,
        create_session_factory,
        init_db,
    )

    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    return engine, create_session_factory(engine)


def _scenario_round1() -> tuple[list[dict], dict, dict]:
    nodes = [file_node(f"m{i}.py", community_id=i) for i in range(4)]
    nodes += [symbol_node(f"m{i}.py::f", pagerank=0.01 * i) for i in range(4)]
    metrics = {
        f"m{i}.py": {
            "pagerank": 0.1 * i,
            "betweenness": 0.2 * i,
            "community_id": i,
            "in_degree": i,
            "out_degree": i + 1,
        }
        for i in range(4)
    }
    membership = {
        f"m{i}.py": {
            "node_type": "file",
            "scc_id": 0,
            "scc_size": 4,
            "symbol_community_id": None,
        }
        for i in range(4)
    } | {
        f"m{i}.py::f": {
            "node_type": "symbol",
            "scc_id": None,
            "scc_size": 0,
            "symbol_community_id": 3,
        }
        for i in range(4)
    }
    return nodes, metrics, membership


def _scenario_round2() -> tuple[list[dict], dict, dict]:
    """m0 untouched, m1 relabelled, m2 centrality moves well past tolerance,
    m3 loses its symbol, and a new m4 arrives. No sub-tolerance moves: those
    are a deliberate divergence and are tested on their own."""
    nodes = [
        file_node("m0.py", community_id=0),
        file_node("m1.py", community_id=99, community_meta_json='{"label": "moved"}'),
        file_node("m2.py", community_id=2, pagerank=0.99, betweenness=0.77),
        file_node("m3.py", community_id=3, is_test=True, language="go"),
        file_node("m4.py", community_id=4),
    ]
    nodes += [symbol_node(f"m{i}.py::f", pagerank=0.01 * i) for i in range(3)]
    nodes += [symbol_node("m4.py::g", name="g", qualified_name="m4.g", file_path="m4.py")]
    metrics = {
        "m0.py": {
            "pagerank": 0.0,
            "betweenness": 0.0,
            "community_id": 0,
            "in_degree": 0,
            "out_degree": 1,
        },
        "m1.py": {
            "pagerank": 0.1,
            "betweenness": 0.2,
            "community_id": 99,
            "in_degree": 1,
            "out_degree": 2,
        },
        "m2.py": {
            "pagerank": 0.55,
            "betweenness": 0.66,
            "community_id": 2,
            "in_degree": 2,
            "out_degree": 3,
        },
        "m4.py": {
            "pagerank": 0.4,
            "betweenness": 0.4,
            "community_id": 4,
            "in_degree": 0,
            "out_degree": 0,
        },
    }
    membership = {
        "m0.py": {
            "node_type": "file",
            "scc_id": 0,
            "scc_size": 4,
            "symbol_community_id": None,
        },
        "m1.py": {
            "node_type": "file",
            "scc_id": 1,
            "scc_size": 2,
            "symbol_community_id": None,
        },
        "m4.py::g": {
            "node_type": "symbol",
            "scc_id": None,
            "scc_size": 0,
            "symbol_community_id": 11,
        },
    }
    return nodes, metrics, membership


async def _run_scenario(session_factory, rid_name: str, *, ungated: bool):
    from repowise.core.persistence import get_session, upsert_repository
    from repowise.core.persistence.crud import graph as graph_crud

    original = graph_crud._batch_upsert_keyed

    async def no_gate(*args, **kwargs):
        kwargs.pop("gate", None)
        return await original(*args, **kwargs)

    if ungated:
        graph_crud._batch_upsert_keyed = no_gate
    try:
        async with get_session(session_factory) as s:
            repo = await upsert_repository(s, name=rid_name, local_path=f"/tmp/{rid_name}")
            rid = repo.id
        for nodes, metrics, membership in (_scenario_round1(), _scenario_round2()):
            async with get_session(session_factory) as s:
                await graph_crud.batch_upsert_graph_nodes(s, rid, nodes)
                await graph_crud.batch_upsert_graph_metrics(s, rid, metrics)
                await graph_crud.batch_upsert_graph_node_membership(s, rid, membership)
        return rid
    finally:
        graph_crud._batch_upsert_keyed = original


async def test_gated_and_ungated_paths_leave_identical_rows():
    from repowise.core.persistence import get_session

    eng_a, sf_a = await _fresh_db()
    eng_b, sf_b = await _fresh_db()
    try:
        rid_a = await _run_scenario(sf_a, "gated", ungated=False)
        rid_b = await _run_scenario(sf_b, "ungated", ungated=True)

        async with get_session(sf_a) as sa, get_session(sf_b) as sb:
            for reader in (_nodes, _metrics, _membership):
                rows_a = await reader(sa, rid_a)
                rows_b = await reader(sb, rid_b)
                assert set(rows_a) == set(rows_b), reader.__name__
                for key in rows_a:
                    a = dict(rows_a[key])
                    b = dict(rows_b[key])
                    a.pop("repository_id")
                    b.pop("repository_id")
                    assert a == b, f"{reader.__name__}:{key}"
    finally:
        await eng_a.dispose()
        await eng_b.dispose()
