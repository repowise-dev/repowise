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
from repowise.server.mcp_server.tool_symbol import (
    _CALLEE_CHAR_BUDGET,
    _MAX_CALLEE_DEPTH,
    _MAX_CALLEES_PER_HOP,
    _expand_callees,
)

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


async def test_a_base_class_is_not_served_as_a_callee(
    session, repository, chain
) -> None:
    """The walk follows `calls`, not everything that reaches a symbol.

    `_expand_callees` imports `_CALL_EDGE_TYPES` from `tool_context.enrichment`,
    which used to be the whole of `SYMBOL_USE_EDGE_TYPES`. So a base class an
    implementation extends, and a framework-bound fixture, were served as
    "callees" **with their source bodies**, for up to three hops, against a
    24,000-char budget. Widening that constant again would silently regress
    this, so it is pinned from this side too.
    """
    rid = repository.id
    for i, (name, edge_type) in enumerate((("Base", "extends"), ("wire", "framework_binds"))):
        sid = f"{_FILE}::{name}"
        session.add(
            WikiSymbol(
                id=f"nc{i}",
                repository_id=rid,
                file_path=_FILE,
                symbol_id=sid,
                name=name,
                qualified_name=f"chain.{name}",
                kind="class",
                signature=f"class {name}",
                start_line=1,
                end_line=2,
                language="python",
            )
        )
        session.add(
            GraphNode(
                id=f"nn{i}",
                repository_id=rid,
                node_id=sid,
                node_type="symbol",
                name=name,
                file_path=_FILE,
                language="python",
            )
        )
        session.add(
            GraphEdge(
                id=f"ne{i}",
                repository_id=rid,
                source_node_id=f"{_FILE}::f0",
                target_node_id=sid,
                edge_type=edge_type,
                confidence=0.99,
            )
        )
    await session.flush()

    root = await _root(session, rid)
    block = await _expand_callees(session, rid, root, chain, 2, None)

    assert [c["symbol_id"] for c in block["callees"]] == [f"{_FILE}::f1"]


# --- the per-hop bounds, measured before they were written -----------------
#
# The tests below pin what a measurement pass found in the walk as it stood: a
# fan-out cap that counted per node rather than per hop, and a character budget
# spent in loop order, so hop 1 consumed everything hop 2 needed. The fixtures
# are the shapes that made each one visible.


def _wide_body(n: int) -> str:
    return "".join(f"    value_{i} = compute(argument_one, argument_two)\n" for i in range(n))


async def _build_graph(session, rid, tmp_path, edges, body_lines):
    """Build a call graph from ``{caller: [callee, ...]}``, one file per symbol.

    One file each so the alphabetical render order is the symbol order, which
    makes "which bodies survived the budget" readable in a failure message.
    """
    names = set(edges) | {d for v in edges.values() for d in v}
    (tmp_path / "src").mkdir(exist_ok=True)
    ids = {}
    for name in sorted(names):
        lines = body_lines if isinstance(body_lines, int) else body_lines.get(name, 40)
        path = f"src/{name}.py"
        (tmp_path / "src" / f"{name}.py").write_text(
            f"def {name}():\n{_wide_body(lines)}", encoding="utf-8"
        )
        ids[name] = f"{path}::{name}"
        session.add(
            WikiSymbol(
                id=f"w-{name}",
                repository_id=rid,
                file_path=path,
                symbol_id=ids[name],
                name=name,
                qualified_name=name,
                kind="function",
                signature=f"def {name}()",
                start_line=1,
                end_line=lines + 1,
                language="python",
                complexity_estimate=1,
            )
        )
        session.add(
            GraphNode(
                id=f"wn-{name}",
                repository_id=rid,
                node_id=ids[name],
                node_type="symbol",
                name=name,
                file_path=path,
                language="python",
            )
        )
    n = 0
    for caller, callees in edges.items():
        for callee in callees:
            session.add(
                GraphEdge(
                    id=f"we-{n}",
                    repository_id=rid,
                    source_node_id=ids[caller],
                    target_node_id=ids[callee],
                    edge_type="calls",
                    confidence=0.9,
                )
            )
            n += 1
    await session.flush()
    return ids


async def _walk(session, rid, tmp_path, root_id, depth):
    from sqlalchemy import select

    res = await session.execute(
        select(WikiSymbol).where(WikiSymbol.repository_id == rid, WikiSymbol.symbol_id == root_id)
    )
    return await _expand_callees(session, rid, res.scalar_one(), tmp_path, depth, None)


def _by_hop(block, served_only=False):
    rows = list(block["callees"]) + ([] if served_only else list(block.get("not_rendered", [])))
    out: dict[int, int] = {}
    for entry in rows:
        if served_only and not entry.get("verified"):
            continue
        out[entry["depth"]] = out.get(entry["depth"], 0) + 1
    return out


async def test_fan_out_is_capped_per_hop_not_per_frontier_node(
    session, repository, tmp_path
) -> None:
    """`_MAX_CALLEES_PER_HOP` has to mean a hop. It used to mean a node.

    The limit was passed to the per-node edge query inside the frontier loop,
    so a hop of N nodes could yield 12xN callees. Measured on exactly this
    shape before the fix: **144 rows at hop 2 against a documented cap of 12**,
    every one of them costing a row query and a not_rendered entry.
    """
    edges = {"w": [f"m{i:02d}" for i in range(12)]}
    for i in range(12):
        edges[f"m{i:02d}"] = [f"z{i:02d}{j:02d}" for j in range(14)]
    await _build_graph(session, repository.id, tmp_path, edges, 40)

    block = await _walk(session, repository.id, tmp_path, "src/w.py::w", 3)
    found = _by_hop(block)
    assert found[1] <= _MAX_CALLEES_PER_HOP
    assert found[2] <= _MAX_CALLEES_PER_HOP, f"hop 2 returned {found[2]} rows against a cap of 12"


async def test_a_deeper_hop_keeps_a_body_when_hop_one_could_spend_it_all(
    session, repository, tmp_path
) -> None:
    """The point of asking for depth 3: hop 2 arrives as source, not as a name.

    The budget used to be consumed in loop order, so large direct callees
    exhausted all 24,000 characters and every hop-2 symbol came back as a
    `fetch_with` reference. Measured on this shape: 10 hop-1 bodies and **zero**
    at hop 2. Each hop now holds a share of the budget.
    """
    edges = {"hub": [f"c{i:02d}" for i in range(20)]}
    for i in range(20):
        edges[f"c{i:02d}"] = [f"g{i:02d}{j}" for j in range(3)]
    await _build_graph(session, repository.id, tmp_path, edges, 90)

    block = await _walk(session, repository.id, tmp_path, "src/hub.py::hub", 3)
    served = _by_hop(block, served_only=True)
    assert served.get(1), "hop 1 lost its evidence to the reservation"
    assert served.get(2), "hop 2 was requested and came back with no body at all"
    assert sum(len(c["source"]) for c in block["callees"]) <= _CALLEE_CHAR_BUDGET


async def test_an_empty_deeper_hop_costs_the_caller_nothing(
    session, repository, tmp_path
) -> None:
    """A reserve held for a hop that does not exist is a body the caller paid for.

    Two large direct callees, both leaves, asked for at depth 3. A per-hop
    reservation taken before the walk knows which hops have rows throttles hop 1
    to half the budget and drops one of the two bodies outright: measured,
    17,402 characters down to 8,701. The reservation is taken only across hops
    discovery actually found, and the remainder is refilled to what it deferred,
    so a depth-3 walk over leaves matches the depth-2 one exactly.
    """
    await _build_graph(session, repository.id, tmp_path, {"L": ["leaf_a", "leaf_b"]}, 200)

    deep = await _walk(session, repository.id, tmp_path, "src/L.py::L", 3)
    shallow = await _walk(session, repository.id, tmp_path, "src/L.py::L", 2)
    assert _by_hop(deep, served_only=True) == _by_hop(shallow, served_only=True) == {1: 2}
    assert "not_rendered" not in deep


async def test_an_edge_to_an_unindexed_symbol_yields_no_entry(
    session, repository, tmp_path
) -> None:
    """A dangling edge must not become a callee with no body and no reference.

    Edges outlive symbol rows, through a deleted function or a file dropped from
    the index, and an entry with a name but nothing to fetch is worse than the
    missing hop: the agent spends a round trip proving it is empty.
    """
    ids = await _build_graph(session, repository.id, tmp_path, {"p0": ["p1"], "p1": ["p2"]}, 40)
    session.add(
        GraphEdge(
            id="we-ghost",
            repository_id=repository.id,
            source_node_id=ids["p0"],
            target_node_id="src/ghost.py::ghost",
            edge_type="calls",
            confidence=0.9,
        )
    )
    await session.flush()

    block = await _walk(session, repository.id, tmp_path, "src/p0.py::p0", 3)
    assert [c["symbol_id"] for c in block["callees"]] == [ids["p1"], ids["p2"]]
    assert "ghost" not in str(block)


async def test_a_diamond_serves_the_shared_callee_once(session, repository, tmp_path) -> None:
    """Two callers reaching the same symbol is one body, at the shallower hop."""
    await _build_graph(
        session, repository.id, tmp_path, {"d0": ["d1", "d2"], "d1": ["d3"], "d2": ["d3"]}, 40
    )

    block = await _walk(session, repository.id, tmp_path, "src/d0.py::d0", 3)
    ids = [c["symbol_id"] for c in block["callees"]]
    assert ids == sorted(set(ids)), "the shared callee was serialised twice"
    assert _by_hop(block) == {1: 2, 2: 1}


async def test_a_hop_with_no_indexed_rows_does_not_renumber_the_hops_behind_it(
    session, repository, tmp_path
) -> None:
    """`depth` is graph distance, not a position in the list of surviving hops.

    An excluded or unindexed middle hop produces no rows. Skipping it when the
    hops are collected, then numbering them by position, serves a grandchild
    claiming to be a direct callee — and an agent that believes the label reads
    the wrong call site.
    """
    import pathspec

    await _build_graph(
        session, repository.id, tmp_path, {"g0": ["g1"], "g1": ["g2"]}, 40
    )
    spec = pathspec.PathSpec.from_lines("gitwildmatch", ["src/g1.py"])
    from sqlalchemy import select

    res = await session.execute(
        select(WikiSymbol).where(
            WikiSymbol.repository_id == repository.id,
            WikiSymbol.symbol_id == "src/g0.py::g0",
        )
    )
    block = await _expand_callees(session, repository.id, res.scalar_one(), tmp_path, 3, spec)

    assert [c["symbol_id"] for c in block["callees"]] == ["src/g2.py::g2"]
    assert block["callees"][0]["depth"] == 2, "a grandchild was served as a direct callee"


async def test_the_fan_out_cap_is_spent_on_rows_that_can_actually_be_served(
    session, repository, tmp_path
) -> None:
    """Excluded targets must not consume slots the hop cap could have given away.

    Two frontier nodes, eight callees each, against a cap of twelve. One node's
    callees are all excluded and all sort first. Cutting the ids before the row
    lookup spends eight of the twelve slots on rows that are then filtered out,
    and the caller loses half the evidence the hop actually had.

    Scoped to a multi-node frontier deliberately: the per-node edge query
    carries its own limit, so a single node whose top edges are all excluded is
    truncated before this function sees them, and that bound is not this
    function's to fix.
    """
    import pathspec

    edges = {
        "root": ["m_a", "m_b"],
        "m_a": [f"a_vendor_{i}" for i in range(8)],
        "m_b": [f"z_real_{i}" for i in range(8)],
    }
    await _build_graph(session, repository.id, tmp_path, edges, 40)
    spec = pathspec.PathSpec.from_lines("gitwildmatch", ["src/a_vendor_*.py"])
    from sqlalchemy import select

    res = await session.execute(
        select(WikiSymbol).where(
            WikiSymbol.repository_id == repository.id,
            WikiSymbol.symbol_id == "src/root.py::root",
        )
    )
    block = await _expand_callees(session, repository.id, res.scalar_one(), tmp_path, 3, spec)

    hop_two = [c for c in block["callees"] if c["depth"] == 2]
    hop_two += [c for c in block.get("not_rendered", []) if c["depth"] == 2]
    names = sorted(c["name"] for c in hop_two)
    assert len(hop_two) == 8, f"excluded targets ate slots the hop had to give: {names}"
    assert all(c["name"].startswith("z_real_") for c in hop_two)


async def test_a_capped_hop_says_how_many_callees_it_dropped(
    session, repository, tmp_path
) -> None:
    """A cap this module cannot make recoverable must at least be visible.

    Listing every dropped callee is the payload the cap exists to prevent, so a
    count is what is reported. Silence would read as "this is the whole
    fan-out", which is the failure the not_rendered list already exists to
    avoid.

    The count is a floor, not a total: the per-node edge query carries its own
    limit, so callees it never handed over are not counted here either.
    """
    edges = {"w": [f"m{i:02d}" for i in range(12)]}
    for i in range(12):
        edges[f"m{i:02d}"] = [f"z{i:02d}{j:02d}" for j in range(14)]
    await _build_graph(session, repository.id, tmp_path, edges, 40)

    block = await _walk(session, repository.id, tmp_path, "src/w.py::w", 3)
    capped = {row["depth"]: row["omitted"] for row in block["fan_out_capped"]}
    assert capped[2] > 0, "hop 2 dropped callees and said nothing"
    served_at_two = [c for c in block["callees"] if c["depth"] == 2]
    dropped_at_two = [c for c in block.get("not_rendered", []) if c["depth"] == 2]
    # 12 frontier nodes calling 14 each is a true fan-out of 168, but the
    # per-node edge query hands over 12 apiece, so 144 is all the walk ever
    # sees. Everything it does see is accounted for — served, deferred, or
    # counted — and the 24 it never saw are why the count is a floor.
    assert len(served_at_two) + len(dropped_at_two) + capped[2] == 12 * 12


async def test_budget_a_deeper_hop_leaves_unspent_returns_to_the_direct_callees(
    session, repository, tmp_path
) -> None:
    """A hop-1 body over its own share still outranks a hop-2 body for leftovers.

    If the reservation only bounds the shallow hops, an unspent share flows
    forward: a direct callee one character over its slice is deferred, the
    deeper hop drains what is left, and the single most relevant body in the
    response is the one that comes back as a reference. The hop-ordered refill
    is what prevents that, so the shape is pinned from the losing side.
    """
    edges = {"r": ["fat_one"], "fat_one": [f"small_{i}" for i in range(6)]}
    bodies = {"fat_one": 150}
    for i in range(6):
        bodies[f"small_{i}"] = 20
    await _build_graph(session, repository.id, tmp_path, edges, bodies)

    block = await _walk(session, repository.id, tmp_path, "src/r.py::r", 3)
    served = {c["name"] for c in block["callees"]}
    assert "fat_one" in served, "the direct callee lost its body to the hop behind it"
    assert any(c["depth"] == 2 for c in block["callees"]), "hop 2 got nothing"
