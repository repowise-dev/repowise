"""Ties must be broken on content, not on iteration order.

Scores collide constantly: every leaf file shares a PageRank, every isolate is
a size-1 community, every same-length cycle scores the same. Python's sort is
stable, so a score-only key leaves tied entries in whatever order they arrived
in — and they arrive in graph insertion order, which varies between runs
because parsing is pooled and co-change edges land in thread-completion order.

The effect is not cosmetic: the allocator slices the sorted candidate list, so
a shuffled tie changes *which pages get generated*, and a shuffled community
list changes the ids that name module pages. These tests feed identical input
in two different orders and require identical output.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.analysis.communities import detect_file_communities
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.selection import SelectionInputs, select_pages

from .test_selection_budget import FakeFileInfo, FakeParsedFile, FakeSymbol


def _tied_repo(n_files: int = 60):
    """A repo where every file scores identically — all ties, no signal."""
    parsed = [
        FakeParsedFile(
            file_info=FakeFileInfo(path=f"pkg{i // 20}/module_{i:02d}.py"),
            symbols=[FakeSymbol(name=f"handler_{i:02d}")],
        )
        for i in range(n_files)
    ]
    pagerank = {p.file_info.path: 0.5 for p in parsed}
    betweenness = {p.file_info.path: 0.25 for p in parsed}
    community = {p.file_info.path: 0 for p in parsed}
    return parsed, pagerank, betweenness, community


def _select(parsed, pagerank, betweenness, community):
    cfg = GenerationConfig(coverage_pct=0.30)
    return select_pages(
        SelectionInputs(
            parsed_files=parsed,
            pagerank=pagerank,
            betweenness=betweenness,
            community=community,
            community_info=None,
            sccs=[],
            git_meta_map=None,
            config=cfg,
        )
    )


class TestSelectionTies:
    def test_file_pages_survive_input_reordering(self):
        parsed, pr, bet, comm = _tied_repo()
        forward = _select(parsed, pr, bet, comm)
        reverse = _select(list(reversed(parsed)), pr, bet, comm)
        assert set(forward.file_page_paths) == set(reverse.file_page_paths)

    def test_symbol_spotlights_survive_input_reordering(self):
        parsed, pr, bet, comm = _tied_repo()
        forward = _select(parsed, pr, bet, comm)
        reverse = _select(list(reversed(parsed)), pr, bet, comm)
        assert set(forward.symbol_spotlights) == set(reverse.symbol_spotlights)

    def test_budget_actually_drops_some_files(self):
        """Guards the guard: if coverage took everything, the tests above
        would pass no matter how the candidate list was ordered."""
        parsed, pr, bet, comm = _tied_repo()
        sel = _select(parsed, pr, bet, comm)
        assert 0 < len(sel.file_page_paths) < len(parsed)


class TestCommunityTies:
    def _graph(self, edge_order):
        g = nx.DiGraph()
        for u, v in edge_order:
            g.add_node(u, node_type="file", language="python")
            g.add_node(v, node_type="file", language="python")
            g.add_edge(u, v, edge_type="imports")
        return g

    def test_equal_sized_communities_get_stable_ids(self):
        """Three disconnected pairs: identical size, so the id each pair gets
        is decided entirely by the tiebreak. The community id is a module
        page's target_path, so a shuffled id deletes and recreates the page."""
        edges = [
            ("alpha/a.py", "alpha/b.py"),
            ("bravo/a.py", "bravo/b.py"),
            ("charlie/a.py", "charlie/b.py"),
        ]
        forward, _, _ = detect_file_communities(self._graph(edges))
        reverse, _, _ = detect_file_communities(self._graph(list(reversed(edges))))
        assert forward == reverse

    def test_reindex_is_a_total_order_on_equal_sizes(self):
        """Goes straight at the re-index step, which is where the id is minted.

        The graph-level tests above cannot fail while the partition input
        happens to be sorted upstream, but Leiden (tried before Louvain, and
        absent from CI) returns a partition ordered by its own internals. This
        asserts the property the re-index has to hold on its own."""
        from repowise.core.analysis.communities import detect_file_communities as _detect

        groups = [["b/1.py", "b/2.py"], ["a/1.py", "a/2.py"], ["c/1.py", "c/2.py"]]

        def rank(order):
            g = nx.DiGraph()
            for members in order:
                for p in members:
                    g.add_node(p, node_type="file", language="python")
                g.add_edge(members[0], members[1], edge_type="imports")
            return _detect(g)[0]

        # Same three communities, three different arrival orders, one answer.
        assert rank(groups) == rank(list(reversed(groups))) == rank([groups[i] for i in (1, 2, 0)])

    def test_community_labels_are_stable(self):
        edges = [
            ("alpha/a.py", "alpha/b.py"),
            ("bravo/a.py", "bravo/b.py"),
            ("charlie/a.py", "charlie/b.py"),
        ]
        _, forward, _ = detect_file_communities(self._graph(edges))
        _, reverse, _ = detect_file_communities(self._graph(list(reversed(edges))))
        assert {cid: ci.label for cid, ci in forward.items()} == {
            cid: ci.label for cid, ci in reverse.items()
        }

    def test_isolates_get_stable_ids(self):
        """Every isolate is its own size-1 community — the worst tie case."""

        def assign(paths):
            g = nx.DiGraph()
            for p in paths:
                g.add_node(p, node_type="file", language="python")
            return detect_file_communities(g)[0]

        paths = [f"pkg/mod_{i:02d}.py" for i in range(10)]
        assert assign(paths) == assign(list(reversed(paths)))

    def test_test_files_attach_stably(self):
        """A test file linked to two production files must pick the same one
        regardless of which edge the graph happened to store first."""

        def assign(edge_order):
            g = nx.DiGraph()
            for u, v in edge_order:
                g.add_node(u, node_type="file", language="python")
                g.add_node(v, node_type="file", language="python")
                g.add_edge(u, v, edge_type="imports")
            return detect_file_communities(g)[0]["tests/test_thing.py"]

        edges = [
            ("alpha/a.py", "alpha/b.py"),
            ("bravo/a.py", "bravo/b.py"),
            ("tests/test_thing.py", "alpha/a.py"),
            ("tests/test_thing.py", "bravo/a.py"),
        ]
        assert assign(edges) == assign(list(reversed(edges)))
