"""`repowise update` must retire the cycles a fixed engine no longer finds.

The engine fix for #1294 only changes what the in-memory graph says. Three
persisted artifacts had no path back to agreement with it on an incremental
update, so a user who upgraded and ran `update` would keep being served the
false cycles: the `scc_page` rows, the `graph_node_membership` rows behind the
Stats "largest cycle" record, and the `graph_edges` rows the health engine
rehydrates. These cover all three.
"""

from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.generation.models import compute_page_id, scc_page_slug
from repowise.core.persistence import (
    batch_upsert_graph_node_membership,
    get_scc_members,
)
from repowise.core.persistence.models import GraphNodeMembership, Page
from repowise.core.pipeline.persist import sweep_absent_cycle_pages
from tests.unit.persistence.helpers import insert_repo, make_page_kwargs


@pytest.fixture
async def repo_id(async_session) -> str:
    repo = await insert_repo(async_session)
    return repo.id


class _FakeBuilder:
    """Minimal stand-in exposing only what the sweep reads."""

    def __init__(self, sccs: list[set[str]], graph: nx.DiGraph | None = None) -> None:
        self._sccs = sccs
        self._graph = graph if graph is not None else nx.DiGraph()

    def strongly_connected_components(self):
        return [frozenset(s) for s in self._sccs]

    def graph(self):
        return self._graph


async def _add_scc_page(async_session, repo_id: str, members: list[str]) -> str:
    from repowise.core.persistence.crud import upsert_page

    slug = scc_page_slug(sorted(members))
    await upsert_page(
        async_session,
        **make_page_kwargs(
            repo_id,
            page_id=compute_page_id("scc_page", slug),
            page_type="scc_page",
            target_path=slug,
            title="Circular Dependency",
        ),
    )
    await async_session.flush()
    return compute_page_id("scc_page", slug)


@pytest.mark.asyncio
class TestCyclePageSweep:
    async def test_page_for_a_vanished_cycle_is_deleted(self, async_session, repo_id) -> None:
        ghost = await _add_scc_page(async_session, repo_id, ["acl/acl.go", "acl/user.go"])
        # The rebuilt graph finds no cycle at all.
        swept = await sweep_absent_cycle_pages(async_session, repo_id, _FakeBuilder([]))
        assert swept == [ghost]
        remaining = (await async_session.execute(Page.__table__.select())).fetchall()
        assert not [r for r in remaining if r.page_type == "scc_page"]

    async def test_page_for_a_surviving_cycle_is_kept(self, async_session, repo_id) -> None:
        members = ["a.py", "b.py"]
        live = await _add_scc_page(async_session, repo_id, members)
        swept = await sweep_absent_cycle_pages(async_session, repo_id, _FakeBuilder([set(members)]))
        assert swept == []
        rows = (await async_session.execute(Page.__table__.select())).fetchall()
        assert [r.id for r in rows if r.page_type == "scc_page"] == [live]

    async def test_shrunken_cycle_retires_the_old_id(self, async_session, repo_id) -> None:
        # Membership is the page's identity, so a cycle that loses a member is
        # a different page: the old row must not linger as a duplicate.
        old = await _add_scc_page(async_session, repo_id, ["a.py", "b.py", "c.py"])
        swept = await sweep_absent_cycle_pages(
            async_session, repo_id, _FakeBuilder([{"a.py", "b.py"}])
        )
        assert swept == [old]

    async def test_missing_builder_is_a_no_op(self, async_session, repo_id) -> None:
        page = await _add_scc_page(async_session, repo_id, ["a.py", "b.py"])
        assert await sweep_absent_cycle_pages(async_session, repo_id, None) == []
        rows = (await async_session.execute(Page.__table__.select())).fetchall()
        assert [r.id for r in rows] == [page]


@pytest.mark.asyncio
class TestMembershipPrune:
    async def test_node_that_left_a_cycle_loses_its_row(self, async_session, repo_id) -> None:
        await batch_upsert_graph_node_membership(
            async_session,
            repo_id,
            {
                "a.go": {"node_type": "file", "scc_id": 0, "scc_size": 2},
                "b.go": {"node_type": "file", "scc_id": 0, "scc_size": 2},
            },
        )
        assert set((await get_scc_members(async_session, repo_id)).get(0, [])) == {"a.go", "b.go"}

        # Re-run after the fix: the cycle is gone, so the snapshot is empty.
        await batch_upsert_graph_node_membership(async_session, repo_id, {})
        rows = (
            await async_session.execute(
                GraphNodeMembership.__table__.select().where(
                    GraphNodeMembership.repository_id == repo_id
                )
            )
        ).fetchall()
        assert rows == []
        assert (await get_scc_members(async_session, repo_id)) == {}

    async def test_surviving_nodes_are_kept_and_updated(self, async_session, repo_id) -> None:
        await batch_upsert_graph_node_membership(
            async_session,
            repo_id,
            {
                "a.go": {"node_type": "file", "scc_id": 0, "scc_size": 3},
                "b.go": {"node_type": "file", "scc_id": 0, "scc_size": 3},
                "c.go": {"node_type": "file", "scc_id": 0, "scc_size": 3},
            },
        )
        await batch_upsert_graph_node_membership(
            async_session,
            repo_id,
            {
                "a.go": {"node_type": "file", "scc_id": 0, "scc_size": 2},
                "b.go": {"node_type": "file", "scc_id": 0, "scc_size": 2},
            },
        )
        assert set((await get_scc_members(async_session, repo_id)).get(0, [])) == {"a.go", "b.go"}
