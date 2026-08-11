"""get_symbol(depth=N) serves a call chain in one response.

Following a chain by hand costs one round trip per hop, and the graph already
holds every edge before the first call is made. ``depth`` spends the edges it
already has instead of the caller's round trips.

The tests below pin the walk itself and, more importantly, its bounds: an
unbounded graph walk on a hub symbol is a worse failure than the round trips
it replaces, so every limit is checked from the side that would blow up.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode, Repository, WikiSymbol
from repowise.server.mcp_server import tool_symbol
from repowise.server.mcp_server.tool_symbol import _MAX_CALLEE_DEPTH, _expand_callees

_FILE = "src/chain.py"
_SRC = "\n".join(f"def f{i}():\n    return f{i + 1}()\n" for i in range(6))


@pytest.fixture
async def repository(session, populated_db) -> Repository:
    return await session.get(Repository, populated_db)


@pytest.fixture
async def chain(session, populated_db, tmp_path):
    """A -> B -> C -> D linear call chain, indexed as symbols, nodes and edges.

    Linear on purpose: the hand-walks this feature replaces are chains, so a
    chain is what the depth semantics have to be correct about.
    """
    rid = populated_db
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "chain.py").write_text(_SRC, encoding="utf-8")

    names = ["f0", "f1", "f2", "f3"]
    for i, n in enumerate(names):
        sid = f"{_FILE}::{n}"
        session.add(
            WikiSymbol(
                id=f"cs{i}",
                repository_id=rid,
                file_path=_FILE,
                symbol_id=sid,
                name=n,
                qualified_name=f"chain.{n}",
                kind="function",
                signature=f"def {n}()",
                start_line=i * 3 + 1,
                end_line=i * 3 + 2,
                language="python",
                complexity_estimate=1,
            )
        )
        session.add(
            GraphNode(
                id=f"cn{i}",
                repository_id=rid,
                node_id=sid,
                node_type="symbol",
                name=n,
                file_path=_FILE,
                language="python",
            )
        )
    for i in range(len(names) - 1):
        session.add(
            GraphEdge(
                id=f"ce{i}",
                repository_id=rid,
                source_node_id=f"{_FILE}::{names[i]}",
                target_node_id=f"{_FILE}::{names[i + 1]}",
                edge_type="calls",
                confidence=0.9,
            )
        )
    await session.flush()
    return tmp_path


async def _root(session, rid: str) -> WikiSymbol:
    from sqlalchemy import select

    res = await session.execute(
        select(WikiSymbol).where(
            WikiSymbol.repository_id == rid, WikiSymbol.symbol_id == f"{_FILE}::f0"
        )
    )
    return res.scalar_one()


async def test_depth_two_serves_the_direct_callee_with_its_body(
    session, repository, chain
) -> None:
    """The whole point: the next hop arrives with source, not as a name to re-fetch."""
    root = await _root(session, repository.id)
    block = await _expand_callees(session, repository.id, root, chain, 2, None)

    assert [c["symbol_id"] for c in block["callees"]] == [f"{_FILE}::f1"]
    entry = block["callees"][0]
    assert entry["depth"] == 1
    assert "def f1" in entry["source"]
    assert entry["verified"] is True


async def test_depth_three_reaches_two_hops_and_labels_each(
    session, repository, chain
) -> None:
    """Transitive, and each body says how far out it is."""
    root = await _root(session, repository.id)
    block = await _expand_callees(session, repository.id, root, chain, 3, None)

    by_depth = {c["symbol_id"]: c["depth"] for c in block["callees"]}
    assert by_depth == {f"{_FILE}::f1": 1, f"{_FILE}::f2": 2}


async def test_depth_one_does_no_walk_at_all(session, repository, chain) -> None:
    """Default behaviour is unchanged: no edges are read, nothing is attached."""
    root = await _root(session, repository.id)
    assert await _expand_callees(session, repository.id, root, chain, 1, None) is None


async def test_a_leaf_symbol_returns_no_block(session, repository, chain) -> None:
    """No outbound edges must mean no empty block, not an empty list."""
    from sqlalchemy import select

    res = await session.execute(
        select(WikiSymbol).where(
            WikiSymbol.repository_id == repository.id,
            WikiSymbol.symbol_id == f"{_FILE}::f3",
        )
    )
    leaf = res.scalar_one()
    assert await _expand_callees(session, repository.id, leaf, chain, 3, None) is None


# --- the bounds, checked from the side that would blow up ------------------


async def test_a_cycle_terminates_and_serves_each_symbol_once(
    session, repository, chain
) -> None:
    """Recursion is normal code. A walk that re-queues a seen node never ends.

    Closing f3 -> f0 makes the chain a cycle; the walk must stop and must not
    serve the root or any node twice.
    """
    session.add(
        GraphEdge(
            id="ce-cycle",
            repository_id=repository.id,
            source_node_id=f"{_FILE}::f3",
            target_node_id=f"{_FILE}::f0",
            edge_type="calls",
            confidence=0.9,
        )
    )
    await session.flush()

    root = await _root(session, repository.id)
    block = await _expand_callees(session, repository.id, root, chain, _MAX_CALLEE_DEPTH, None)
    ids = [c["symbol_id"] for c in block["callees"]]
    assert len(ids) == len(set(ids)), "a symbol was served twice"
    assert f"{_FILE}::f0" not in ids, "the root came back as its own callee"


async def test_low_confidence_edges_are_not_followed(session, repository, chain) -> None:
    """The confidence floor exists because tier-3 resolution invents edges.

    A guessed edge that drags a whole unrelated body into the payload is worse
    than the missing hop it papers over.
    """
    session.add(
        GraphEdge(
            id="ce-weak",
            repository_id=repository.id,
            source_node_id=f"{_FILE}::f0",
            target_node_id="src/auth/service.py::AuthService",
            edge_type="calls",
            confidence=0.1,
        )
    )
    await session.flush()

    root = await _root(session, repository.id)
    block = await _expand_callees(session, repository.id, root, chain, 2, None)
    assert [c["symbol_id"] for c in block["callees"]] == [f"{_FILE}::f1"]


async def test_char_budget_lists_the_dropped_callee_instead_of_hiding_it(
    session, repository, chain, monkeypatch
) -> None:
    """Silent truncation reads as "this is the whole chain". It must not.

    Squeezed to a budget that fits nothing, the callee still appears, with the
    exact range read that fetches it.
    """
    monkeypatch.setattr(tool_symbol, "_CALLEE_CHAR_BUDGET", 1)
    root = await _root(session, repository.id)
    block = await _expand_callees(session, repository.id, root, chain, 2, None)

    assert block["callees"] == []
    dropped = block["not_rendered"][0]
    assert dropped["symbol_id"] == f"{_FILE}::f1"
    assert dropped["fetch_with"] == f"{_FILE}:4-5"
    assert "not_rendered" in block["note"]


# --- the wiring, through the tool itself -----------------------------------


async def test_get_symbol_attaches_callee_bodies_and_clamps_depth(
    session, setup_mcp, chain, monkeypatch
) -> None:
    """The parameter has to survive the tool boundary, not just the helper.

    Also pins the clamp: an out-of-range depth is a caller reaching for more
    of the chain, so it is bounded rather than rejected.
    """
    import repowise.server.mcp_server as mcp_mod
    from repowise.server.mcp_server.tool_symbol import get_symbol

    monkeypatch.setattr(mcp_mod, "_repo_path", str(chain))
    await session.commit()

    plain = await get_symbol(symbol_id=f"{_FILE}::f0")
    assert "callee_bodies" not in plain, "depth defaults to no walk"

    walked = await get_symbol(symbol_id=f"{_FILE}::f0", depth=99)
    block = walked["callee_bodies"]
    assert block["depth"] == _MAX_CALLEE_DEPTH
    assert {c["name"] for c in block["callees"]} == {"f1", "f2"}
    # The root's own body is still the primary payload.
    assert "def f0" in walked["source"]


async def test_excluded_callees_are_dropped(session, repository, chain) -> None:
    """Exclusion is a boundary; a graph walk must not route around it."""
    import pathspec

    spec = pathspec.PathSpec.from_lines("gitwildmatch", ["src/chain.py"])
    root = await _root(session, repository.id)
    block = await _expand_callees(session, repository.id, root, chain, 3, spec)
    assert block is None
