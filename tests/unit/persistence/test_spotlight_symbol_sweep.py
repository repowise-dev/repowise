"""`repowise update` retires the spotlight of a symbol that left a live file.

Deleting a file tombstones its spotlights. Moving a function to another module
while the old file stays did not: the old ``<path>::<name>`` row stayed fresh
and kept serving the old body and importer list.
"""

from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.generation.models import compute_page_id
from repowise.core.persistence.models import Page
from repowise.core.pipeline.persist import sweep_absent_symbol_spotlights
from tests.unit.persistence.helpers import insert_repo, make_page_kwargs


class _FakeBuilder:
    def __init__(self, graph: nx.DiGraph) -> None:
        self._graph = graph

    def graph(self):
        return self._graph


def _graph(symbols: dict[str, list[str]]) -> nx.DiGraph:
    g = nx.DiGraph()
    for path, names in symbols.items():
        g.add_node(path, node_type="file")
        g.add_node(
            f"{path}::__module__",
            node_type="symbol",
            kind="module",
            name="__module__",
            file_path=path,
        )
        for name in names:
            g.add_node(
                f"{path}::{name}",
                node_type="symbol",
                kind="function",
                name=name,
                file_path=path,
            )
    return g


@pytest.fixture
async def repo_id(async_session) -> str:
    repo = await insert_repo(async_session)
    return repo.id


async def _add_spotlight(async_session, repo_id: str, target: str) -> str:
    from repowise.core.persistence.crud import upsert_page

    page_id = compute_page_id("symbol_spotlight", target)
    await upsert_page(
        async_session,
        **make_page_kwargs(
            repo_id,
            page_id=page_id,
            page_type="symbol_spotlight",
            target_path=target,
            title=target.split("::")[-1],
        ),
    )
    await async_session.flush()
    return page_id


async def _spotlight_ids(async_session) -> set[str]:
    rows = (await async_session.execute(Page.__table__.select())).fetchall()
    return {r.id for r in rows if r.page_type == "symbol_spotlight"}


@pytest.mark.asyncio
class TestAbsentSymbolSpotlightSweep:
    async def test_moved_symbol_page_is_deleted(self, async_session, repo_id) -> None:
        moved = await _add_spotlight(async_session, repo_id, "pkg/contracts.py::match")
        kept = await _add_spotlight(async_session, repo_id, "pkg/contracts.py::load")
        builder = _FakeBuilder(_graph({"pkg/contracts.py": ["load"], "pkg/matching.py": ["match"]}))

        swept = await sweep_absent_symbol_spotlights(async_session, repo_id, builder)

        assert swept == [moved]
        assert await _spotlight_ids(async_session) == {kept}

    async def test_file_outside_graph_keeps_its_pages(self, async_session, repo_id) -> None:
        # Deleted or excluded files belong to the tombstone sweeps, not this one.
        page = await _add_spotlight(async_session, repo_id, "gone.py::f")
        swept = await sweep_absent_symbol_spotlights(
            async_session, repo_id, _FakeBuilder(_graph({"other.py": ["g"]}))
        )
        assert swept == []
        assert await _spotlight_ids(async_session) == {page}

    async def test_file_with_no_parsed_symbols_keeps_its_pages(
        self, async_session, repo_id
    ) -> None:
        # A parse failure leaves only the synthetic module symbol; that is not
        # evidence the symbol is gone.
        page = await _add_spotlight(async_session, repo_id, "broken.py::f")
        swept = await sweep_absent_symbol_spotlights(
            async_session, repo_id, _FakeBuilder(_graph({"broken.py": []}))
        )
        assert swept == []
        assert await _spotlight_ids(async_session) == {page}

    async def test_missing_builder_is_a_no_op(self, async_session, repo_id) -> None:
        page = await _add_spotlight(async_session, repo_id, "a.py::f")
        assert await sweep_absent_symbol_spotlights(async_session, repo_id, None) == []
        assert await _spotlight_ids(async_session) == {page}
