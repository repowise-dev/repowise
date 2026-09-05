"""Community label hygiene: repo-dominant namespace segments never become labels.

The legacy failure mode: a namespace dir present in ~every path (the repo's
own name, ``src``, ``packages``) is not in the hardcoded ``_GENERIC_SEGMENTS``
list, so labels degrade to ``repowise (161)`` and flipped twins like
``ingestion/repowise`` / ``repowise/ingestion``. Labeling now strips
data-driven dominant segments (shared with module naming in kg_curation).
"""

from __future__ import annotations

import itertools

import networkx as nx

from repowise.core.analysis.communities import (
    CommunityInfo,
    _assign_tests_to_communities,
    _conductance,
    _deduplicate_labels,
    _heuristic_label,
    detect_file_communities,
)
from repowise.core.analysis.kg_curation import dominant_segments


def _graph(paths: list[str], edges: list[tuple[str, str]]) -> nx.DiGraph:
    g = nx.DiGraph()
    for p in paths:
        g.add_node(p, node_type="file", language="python")
    for u, v in edges:
        g.add_edge(u, v, edge_type="imports")
    return g


class TestHeuristicLabelStripping:
    def test_dominant_segment_never_becomes_label(self):
        # Dominance is repo-wide: "acme"/"src" appear in every path,
        # "ingestion" only in this community's slice.
        members = [f"acme/src/ingestion/file{i}.py" for i in range(5)]
        repo_paths = members + [f"acme/src/web/w{i}.py" for i in range(5)]
        generic = frozenset(s.lower() for s in dominant_segments(repo_paths))
        assert generic == {"acme", "src"}
        label = _heuristic_label(members, 0, generic)
        assert label == "ingestion"

    def test_flipped_twins_collapse(self):
        # Without stripping these two communities label as "acme/ingestion"
        # and "ingestion/acme" — flipped twins. With stripping both reduce
        # to their informative segment.
        ingest = [f"acme/src/ingestion/p{i}.py" for i in range(4)]
        persist = [f"acme/src/persistence/p{i}.py" for i in range(4)]
        generic = frozenset(
            s.lower() for s in dominant_segments(ingest + persist)
        )
        assert "acme" in generic and "src" in generic
        assert _heuristic_label(ingest, 0, generic) == "ingestion"
        assert _heuristic_label(persist, 1, generic) == "persistence"

    def test_no_extra_generic_preserves_legacy_behavior(self):
        # Default empty set: byte-identical to the pre-change heuristic.
        paths = [f"web/components/c{i}.tsx" for i in range(4)]
        assert _heuristic_label(paths, 0) == _heuristic_label(
            paths, 0, frozenset()
        )

    def test_stem_fallback_skips_dominant_segment(self):
        # Strategy 3 (filename stems) must not resurrect a stripped segment.
        paths = ["acme/acme.py", "acme/acme.pyi"]
        label = _heuristic_label(paths, 7, frozenset({"acme"}))
        assert label == "cluster_7"


class TestDeduplicateLabels:
    def test_sub_label_disambiguation_skips_generic(self):
        a = CommunityInfo(
            community_id=0,
            label="ingestion",
            members=[f"acme/src/ingestion/resolvers/r{i}.py" for i in range(4)],
            size=4,
            cohesion=0.5,
            dominant_language="python",
        )
        b = CommunityInfo(
            community_id=1,
            label="ingestion",
            members=[f"acme/src/ingestion/parsing/p{i}.py" for i in range(4)],
            size=4,
            cohesion=0.5,
            dominant_language="python",
        )
        info = {0: a, 1: b}
        _deduplicate_labels(info, frozenset({"acme", "src"}))
        labels = {info[0].label, info[1].label}
        # Disambiguated by informative sub-segments, never by "acme"/"src".
        assert labels == {"ingestion/resolvers", "ingestion/parsing"}
        for label in labels:
            assert "acme" not in label and "src" not in label


class TestDetectFileCommunitiesLabels:
    def test_end_to_end_labels_strip_repo_namespace(self):
        # Two clusters under a shared monorepo namespace.
        ingest = [f"acme/src/acmepkg/ingestion/m{i}.py" for i in range(6)]
        web = [f"acme/src/acmepkg/web/w{i}.py" for i in range(6)]
        edges = list(itertools.pairwise(ingest)) + list(itertools.pairwise(web))
        g = _graph(ingest + web, edges)
        _, info, _ = detect_file_communities(g)
        labels = [ci.label for ci in info.values()]
        for label in labels:
            for noise in ("acme", "src", "acmepkg"):
                assert noise not in label.split("/"), (
                    f"dominant segment {noise!r} leaked into label {label!r}"
                )
        # The informative segments survive somewhere in the labels.
        joined = " ".join(labels)
        assert "ingestion" in joined and "web" in joined


class TestRootFirstLabels:
    def test_sub_label_follows_path_order(self):
        # Frequency picks "ingestion" (in every path) as primary and "engine"
        # as the sub-segment; the label must still read the way the paths nest.
        paths = [f"engine/ingestion/r{i}.py" for i in range(4)] + [
            "other/ingestion/z.py",
        ]
        assert _heuristic_label(paths, 0) == "engine/ingestion"

    def test_repo_name_is_generic(self):
        # "acme" sits under packages/*/src in half the paths, below the
        # dominant-segment bar, so only the explicit repo name strips it.
        ingest = [f"packages/core/src/acme/ingestion/m{i}.py" for i in range(6)]
        web = [f"packages/web/components/w{i}.tsx" for i in range(6)]
        edges = list(itertools.pairwise(ingest)) + list(itertools.pairwise(web))
        _, info, _ = detect_file_communities(_graph(ingest + web, edges), repo_name="Acme")
        labels = {ci.label for ci in info.values()}
        assert "ingestion" in labels
        for label in labels:
            assert "acme" not in label.split("/")


class TestTestAssignment:
    def test_most_linked_community_wins(self):
        # The test imports one file from community 0 and two from community 1.
        # Alphabetical order would file it under community 0 ("a.py").
        g = nx.DiGraph()
        for p in ["src/a.py", "src/m.py", "src/n.py", "tests/test_x.py"]:
            g.add_node(p, node_type="file")
        for target in ["src/a.py", "src/m.py", "src/n.py"]:
            g.add_edge("tests/test_x.py", target, edge_type="imports")
        prod = {"src/a.py": 0, "src/m.py": 1, "src/n.py": 1}
        assert _assign_tests_to_communities(["tests/test_x.py"], prod, g) == {
            "tests/test_x.py": 1
        }

    def test_ties_break_on_lowest_community_id(self):
        g = nx.DiGraph()
        for p in ["src/a.py", "src/b.py", "tests/test_x.py"]:
            g.add_node(p, node_type="file")
        g.add_edge("tests/test_x.py", "src/b.py", edge_type="imports")
        g.add_edge("tests/test_x.py", "src/a.py", edge_type="imports")
        prod = {"src/a.py": 2, "src/b.py": 1}
        assert _assign_tests_to_communities(["tests/test_x.py"], prod, g) == {
            "tests/test_x.py": 1
        }


class TestConductance:
    def test_cut_over_volume(self):
        g = nx.Graph()
        g.add_edges_from([("a", "b"), ("b", "c"), ("c", "x"), ("a", "y")])
        # intra a-b, b-c (volume 4 counted from both ends); cut c-x, a-y.
        assert _conductance(g, ["a", "b", "c"]) == round(2 / 6, 4)

    def test_none_when_nothing_is_linked(self):
        g = nx.Graph()
        g.add_nodes_from(["a", "b"])
        assert _conductance(g, ["a", "b"]) is None

    def test_detection_reads_it_off_production_members(self):
        prod = [f"src/p{i}.py" for i in range(4)]
        g = _graph([*prod, "tests/test_p.py"], list(itertools.combinations(prod, 2)))
        g.nodes["tests/test_p.py"]["is_test"] = True
        g.add_edge("tests/test_p.py", "src/p0.py", edge_type="imports")
        _, info, _ = detect_file_communities(g)
        (ci,) = [c for c in info.values() if "src/p0.py" in c.members]
        # One production community, all its edges internal: nothing leaves.
        assert ci.conductance == 0.0
        # Labelled from the production members, not the attached test.
        assert ci.label != "tests"


class TestExampleSeparation:
    """Example trees are demoted alongside tests, not partitioned as production."""

    def test_examples_are_demoted_with_tests_not_partitioned(self):
        # An example and a test, neither importing production code. Partitioned
        # as production they would each be an isolate with its own community;
        # demoted they share the non-core catch-all.
        paths = ["src/a1.py", "src/a2.py", "examples/demo.py", "tests/test_a.py"]
        g = nx.DiGraph()
        for path in paths:
            g.add_node(
                path,
                node_type="file",
                language="python",
                is_test=path.startswith("tests/"),
            )
        g.add_edge("src/a1.py", "src/a2.py", edge_type="imports")

        assignment, _info, _algo = detect_file_communities(g)

        assert assignment["examples/demo.py"] == assignment["tests/test_a.py"]
        assert assignment["examples/demo.py"] != assignment["src/a1.py"]

    def test_example_files_keep_an_assignment(self):
        # Demoted, not dropped: every file still lands in a community, so
        # nothing becomes unreachable.
        paths = ["src/a.py", "src/b.py", "examples/demo.py", "benches/bench.py"]
        edges = [("src/a.py", "src/b.py"), ("examples/demo.py", "src/a.py")]
        assignment, _info, _algo = detect_file_communities(_graph(paths, edges))

        assert set(assignment) == set(paths)
        # An example joins the community of the production file it imports.
        assert assignment["examples/demo.py"] == assignment["src/a.py"]
