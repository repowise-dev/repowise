"""External nodes must not be community members (#2538).

``external:`` nodes are stored as file nodes, so the node comprehension in
``detect_file_communities`` used to keep them: they joined the partition and
landed in ``CommunityInfo.members`` and ``size``. Two unrelated files that
both import ``os`` were pulled together through a node that is not part of
the repository.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.analysis.communities import detect_file_communities


def _graph(paths: list[str], edges: list[tuple[str, str]]) -> nx.DiGraph:
    g = nx.DiGraph()
    for p in paths:
        g.add_node(p, node_type="file", language="python")
    for u, v in edges:
        g.add_edge(u, v, edge_type="imports")
    return g


class TestExternalNodesAreNotMembers:
    def test_no_community_member_is_external(self):
        prod = ["pkg/a/f.py", "pkg/b/g.py"]
        externals = ["external:os", "external:pathlib"]
        edges = [(p, e) for p in prod for e in externals]
        assignment, info, _ = detect_file_communities(_graph(prod + externals, edges))

        for ci in info.values():
            assert not any(m.startswith("external:") for m in ci.members)
            assert ci.size == len(ci.members)
        assert set(assignment) == set(prod)

    def test_shared_external_import_does_not_join_two_files(self):
        # Two unrelated files, each importing only ``external:os``. Without the
        # fix the shared external node links them into one community.
        prod = ["pkg/a/f.py", "pkg/b/g.py"]
        edges = [("pkg/a/f.py", "external:os"), ("pkg/b/g.py", "external:os")]
        assignment, info, _ = detect_file_communities(_graph(prod + ["external:os"], edges))

        assert assignment["pkg/a/f.py"] != assignment["pkg/b/g.py"]
        for ci in info.values():
            assert "external:os" not in ci.members
