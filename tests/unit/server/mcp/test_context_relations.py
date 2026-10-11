"""`get_context` must not report a subclass or a fixture as a caller.

The agent surface carried the identical defect the symbol page shipped with:
`_CALL_EDGE_TYPES` was the whole of `SYMBOL_USE_EDGE_TYPES`, poured straight
into `callers`/`callees`. Measured on repowise's own index before the fix:
9.1% of all rows served under `callers` were not calls, and on **705 symbols
every single row was not a call**. The pytest fixture `client` was reported as
having 388 callers, with a note telling the agent to grep for a call site that
does not exist.

These drive the real `get_context` tool rather than the helper, so they fail
if the tool stops emitting the key and not only if the helper changes.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from repowise.core.persistence.models import GraphEdge, GraphNode, Repository

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)

BASE = "app/models.py::Model"
FIXTURE = "tests/conftest.py::client"


async def _seed(session) -> None:
    """A base class with 2 callers and 60 subclasses, and a fixture with none.

    60 exceeds the 50-row call cap on purpose, and the subclasses outrank the
    callers on confidence, so under the old shared cut the callers were not
    merely reordered — they could not be served at all.
    """
    repo = (await session.execute(select(Repository))).scalars().first()
    node_ids = [BASE, FIXTURE, "app/views.py::save", "app/views.py::delete"]
    node_ids += [f"app/sub{i}.py::Sub{i}" for i in range(60)]
    node_ids += [f"tests/test_{i}.py::test_{i}" for i in range(3)]
    for node_id in node_ids:
        session.add(
            GraphNode(
                id=f"rel_{node_id}",
                repository_id=repo.id,
                node_id=node_id,
                node_type="symbol",
                name=node_id.split("::")[-1],
                file_path=node_id.split("::")[0],
                kind="class" if "Sub" in node_id else "function",
                start_line=1,
                end_line=5,
                created_at=_NOW,
            )
        )

    def edge(source: str, target: str, edge_type: str, confidence: float) -> GraphEdge:
        session.add(
            GraphEdge(
                id=f"rel_{source}_{target}_{edge_type}",
                repository_id=repo.id,
                source_node_id=source,
                target_node_id=target,
                edge_type=edge_type,
                confidence=confidence,
                created_at=_NOW,
            )
        )

    for i in range(60):
        edge(f"app/sub{i}.py::Sub{i}", BASE, "extends", 0.99)
    edge("app/views.py::save", BASE, "calls", 0.8)
    edge("app/views.py::delete", BASE, "calls", 0.8)
    for i in range(3):
        edge(f"tests/test_{i}.py::test_{i}", FIXTURE, "framework_binds", 0.9)
    await session.flush()


async def _ctx(target: str) -> dict:
    from repowise.server.mcp_server import get_context

    result = await get_context([target], include=["callers", "callees"], compact=False)
    return result["targets"][target]


@pytest.mark.asyncio
async def test_callers_are_calls_only(setup_mcp, session):
    await _seed(session)

    t = await _ctx(BASE)

    assert {c["edge_type"] for c in t["callers"]} == {"calls"}


@pytest.mark.asyncio
async def test_heritage_cannot_evict_the_real_callers(setup_mcp, session):
    """60 higher-confidence subclasses used to consume the whole 50-row cut."""
    await _seed(session)

    t = await _ctx(BASE)

    assert {c["name"] for c in t["callers"]} == {"save", "delete"}
    assert not t.get("callers_truncated"), "2 of 2 callers is not a truncation"


@pytest.mark.asyncio
async def test_relations_name_each_kind_with_its_true_total(setup_mcp, session):
    await _seed(session)

    t = await _ctx(BASE)

    by_type = {(r["direction"], r["edge_type"]): r for r in t["relations"]}
    assert set(by_type) == {("in", "extends")}
    extends = by_type[("in", "extends")]
    assert extends["group"] == "heritage"
    assert extends["total"] == 60, "the total must be the count, not the row cap"
    assert 0 < len(extends["rows"]) <= 5
    # The group names the edge type once. Repeating it per row was
    # "method_implements" five times for nothing, on a token-budgeted payload.
    assert all("edge_type" not in r for r in extends["rows"])
    # Callers keep it: they have no group above them to carry it.
    assert all(c["edge_type"] == "calls" for c in (await _ctx(BASE))["callers"])


@pytest.mark.asyncio
async def test_a_fixture_nothing_calls_reports_no_callers(setup_mcp, session):
    """The 705-symbol case, and the sharpest one.

    The old code answered this with rows of framework wiring under `callers`,
    a `callers_total`, and a note instructing the agent to grep for a call
    site that does not exist.
    """
    await _seed(session)

    t = await _ctx(FIXTURE)

    assert t["callers"] == []
    assert "callers_total" not in t
    assert "_callers_note" not in t

    wiring = [r for r in t["relations"] if r["edge_type"] == "framework_binds"]
    assert len(wiring) == 1
    assert wiring[0]["group"] == "wiring"
    assert wiring[0]["total"] == 3
    # An empty `callers` beside a populated `relations` otherwise reads as a
    # graph failure rather than as the answer.
    assert "framework_binds" in t["_call_graph_note"]
    assert "Nothing calls" in t["_call_graph_note"]


@pytest.mark.asyncio
async def test_degree_still_counts_every_relation_kind(setup_mcp, session):
    """Degree is "how connected", so it stays over every use edge type even
    though `callers` narrowed to calls."""
    await _seed(session)

    from repowise.server.mcp_server import get_context

    result = await get_context([BASE], include=["metrics"], compact=False)
    metrics = result["targets"][BASE]["metrics"]

    assert metrics["in_degree"] == 62  # 60 extends + 2 calls
    assert metrics["out_degree"] == 0
