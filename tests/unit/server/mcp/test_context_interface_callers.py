"""Callers of an implementation include the calls written against its interface.

A Spring service is injected and called as its interface, so the call lands on
the interface method and the implementation read as called only by its unit
test (Fineract's ``PortfolioCommandSourceWritePlatformServiceImpl``: one test
caller against 121 dependents). ``dispatches_to`` joins the two; get_context
reads it one hop back and marks those rows ``via: "interface"``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from repowise.core.persistence.models import GraphEdge, GraphNode, Repository

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)

IFACE = "svc/Writer.java"
IMPL = "svc/WriterImpl.java"
BASE_RUN = f"{IFACE}::Writer::run"
IMPL_RUN = f"{IMPL}::WriterImpl::run"
TEST = "test/WriterImplTest.java::WriterImplTest::testRun"
N_CALLERS = 25


async def _seed(session) -> None:
    repo = (await session.execute(select(Repository))).scalars().first()

    def node(node_id: str, node_type: str = "symbol") -> None:
        session.add(
            GraphNode(
                id=f"ifc_{node_id}",
                repository_id=repo.id,
                node_id=node_id,
                node_type=node_type,
                name=node_id.split("::")[-1],
                file_path=node_id.split("::")[0],
                kind="method" if node_type == "symbol" else None,
                start_line=1,
                end_line=5,
                created_at=_NOW,
            )
        )

    def edge(source: str, target: str, edge_type: str, confidence: float) -> None:
        session.add(
            GraphEdge(
                id=f"ifc_{source}_{target}_{edge_type}",
                repository_id=repo.id,
                source_node_id=source,
                target_node_id=target,
                edge_type=edge_type,
                confidence=confidence,
                created_at=_NOW,
            )
        )

    for file_id in (IFACE, IMPL):
        node(file_id, "file")
    for sym in (BASE_RUN, IMPL_RUN, TEST):
        node(sym)
    edge(BASE_RUN, IMPL_RUN, "dispatches_to", 0.75)
    edge(TEST, IMPL_RUN, "calls", 0.9)
    for i in range(N_CALLERS):
        caller = f"app/Use{i:02d}.java::Use{i:02d}::go"
        node(caller)
        edge(caller, BASE_RUN, "calls", 0.8)
    await session.flush()


async def _ctx(target: str) -> dict:
    from repowise.server.mcp_server import get_context

    result = await get_context([target], include=["callers"], compact=False)
    return result["targets"][target]


@pytest.mark.asyncio
async def test_file_target_rolls_up_callers_through_the_interface(setup_mcp, session):
    await _seed(session)

    t = await _ctx(IMPL)

    callers = t["callers"]
    # The direct caller ranks first; the interface rows follow under the same cap.
    assert callers[0]["file"] == "test/WriterImplTest.java"
    assert callers[0]["inbound_calls"] == 1 and "via" not in callers[0]
    via = [c for c in callers if c.get("via") == "interface"]
    assert via and all(c["interface_calls"] == 1 for c in via)
    assert t["callers_via_interface"] == N_CALLERS
    assert t["callers_total"] == N_CALLERS + 1
    assert len(callers) == 20 and t["callers_truncated"]
    assert "dependents_count" in t["_call_graph_note"]


@pytest.mark.asyncio
async def test_symbol_target_lists_interface_callers_after_direct_ones(setup_mcp, session):
    await _seed(session)

    t = await _ctx(IMPL_RUN)

    callers = t["callers"]
    assert callers[0]["symbol_id"] == TEST and callers[0].get("via") != "interface"
    via = callers[1:]
    # Interface rows have their own small cap; the rest are counted, not served.
    assert len(via) == 10
    assert all(c["via"] == "interface" and c["via_base"] == BASE_RUN for c in via)
    assert t["callers_via_interface"] == N_CALLERS
    assert t["callers_total"] == N_CALLERS + 1 and t["callers_truncated"]
    assert "via: interface" in t["_callers_note"]


@pytest.mark.asyncio
async def test_interface_callers_alone_still_fill_callers(setup_mcp, session):
    """No direct call edge at all: the implementation is still called."""
    await _seed(session)
    await session.execute(
        GraphEdge.__table__.delete().where(GraphEdge.source_node_id == TEST)
    )
    await session.flush()

    t = await _ctx(IMPL_RUN)

    assert len(t["callers"]) == 10 and t["callers_total"] == N_CALLERS
    assert "Nothing calls" not in t.get("_call_graph_note", "")


@pytest.mark.asyncio
async def test_the_base_method_keeps_only_its_own_callers(setup_mcp, session):
    """The rollup runs from implementation to base, never the other way."""
    await _seed(session)

    t = await _ctx(BASE_RUN)

    assert all(c.get("via") != "interface" for c in t["callers"])
    assert "callers_via_interface" not in t


@pytest.mark.asyncio
async def test_importers_are_never_crowded_out_by_interface_only_files(setup_mcp, session):
    """25 interface-only files must not push an import-only file past the 20 cap."""
    await _seed(session)
    repo = (await session.execute(select(Repository))).scalars().first()
    importers = [f"app/Wire{i}.java" for i in range(3)]
    for path in importers:
        session.add(
            GraphNode(
                id=f"ifc_{path}",
                repository_id=repo.id,
                node_id=path,
                node_type="file",
                name=path.rsplit("/", 1)[-1],
                file_path=path,
                created_at=_NOW,
            )
        )
        session.add(
            GraphEdge(
                id=f"ifc_{path}_imports",
                repository_id=repo.id,
                source_node_id=path,
                target_node_id=IMPL,
                edge_type="imports",
                created_at=_NOW,
            )
        )
    await session.flush()

    t = await _ctx(IMPL)

    shown = [c["file"] for c in t["callers"]]
    assert len(shown) == 20
    assert set(importers) <= set(shown)
    first_via = next(i for i, c in enumerate(t["callers"]) if c.get("via") == "interface")
    assert all(c.get("via") == "interface" for c in t["callers"][first_via:])
