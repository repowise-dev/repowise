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
