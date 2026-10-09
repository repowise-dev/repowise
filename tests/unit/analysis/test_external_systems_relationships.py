"""One package's relationship folds: target evidence, community aggregates, file pages."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from repowise.core.analysis.external_systems import (
    DEFAULT_FILE_LIMIT,
    EVIDENCE_TARGET_LIMIT,
    EXTERNAL_TARGET_LIMIT,
    build_importing_files,
    build_relationship_graph,
    resolve_targets,
    split_community_key,
    split_package_key,
)


@dataclass
class _Declaration:
    ecosystem: str = "npm"
    name: str = "react"
    declared_in: str = "package.json"


@dataclass
class _Link:
    node_id: str
    ecosystem: str = "npm"
    name: str = "react"


@dataclass
class _Edge:
    """Stub of an import edge joined to its importing file's community."""

    source_path: str
    target_node_id: str = "external:react"
    community_id: int = 1
    community_meta_json: str | None = "{}"
    language: str = "ts"


DECLARED = [_Declaration()]
LINKED = [_Link(node_id="external:react")]


def test_undeclared_package_resolves_to_nothing() -> None:
    assert build_relationship_graph(DECLARED, LINKED, [], "npm:missing") is None
    assert build_importing_files(DECLARED, LINKED, [], "npm:missing", "community:1") is None
    assert resolve_targets(DECLARED, LINKED, "npm", "missing", "primary") is None


def test_a_package_declared_only_out_of_scope_is_not_found() -> None:
    declared = [_Declaration(declared_in="local-stash/package.json")]

    assert build_relationship_graph(declared, LINKED, [], "npm:react") is None
    assert build_relationship_graph(declared, LINKED, [], "npm:react", scope="all") is not None


def test_declared_but_unlinked_package_is_an_answerable_empty_graph() -> None:
    graph = build_relationship_graph(DECLARED, [], [], "npm:react")

    assert graph is not None
    assert graph.match_basis == "unresolved"
    assert graph.nodes == []
    assert graph.edges == []
    assert graph.matched_external_nodes_total == 0
    assert graph.evidence_truncated is False
    assert graph.truncated is False
    assert graph.package_node_id == "package:npm:react"


def test_match_basis_names_how_the_target_was_reached() -> None:
    def basis(*node_ids: str) -> str:
        links = [_Link(node_id=node_id) for node_id in node_ids]
        graph = build_relationship_graph(DECLARED, links, [], "npm:react")
        assert graph is not None
        return graph.match_basis

    assert basis("external:react") == "exact"
    assert basis("external:react/jsx-runtime") == "subpath"
    assert basis("external:react:server") == "subpath"
    assert basis("external:preact-compat") == "mapped"
    assert basis("external:react", "external:preact-compat") == "mixed"


def test_target_evidence_is_capped_and_says_so() -> None:
    links = [
        _Link(node_id=f"external:react/m{index:04d}") for index in range(EVIDENCE_TARGET_LIMIT + 7)
    ]

    graph = build_relationship_graph(DECLARED, links, [], "npm:react")

    assert graph is not None
    assert graph.matched_external_nodes_total == EVIDENCE_TARGET_LIMIT + 7
    assert len(graph.matched_external_nodes) == EXTERNAL_TARGET_LIMIT
    assert graph.matched_external_nodes_truncated is True
    assert graph.evidence_target_limit == EVIDENCE_TARGET_LIMIT
    assert graph.evidence_truncated is True
    # Truncated evidence makes the whole graph a partial answer.
    assert graph.truncated is True


def test_communities_are_ranked_by_importers_then_edges() -> None:
    edges = [
        _Edge("src/a.ts", community_id=7),
        _Edge("src/b.ts", community_id=7),
        _Edge("src/c.ts", community_id=3),
        _Edge("src/c.ts", community_id=3),
        _Edge("src/c.ts", community_id=3),
        _Edge("src/d.ts", community_id=9),
    ]

    graph = build_relationship_graph(DECLARED, LINKED, edges, "npm:react")

    assert graph is not None
    assert [n.aggregate_key for n in graph.nodes] == ["community:7", "community:3", "community:9"]
    assert [n.importing_file_count for n in graph.nodes] == [2, 1, 1]
    assert [n.import_edge_count for n in graph.nodes] == [2, 3, 1]
    assert graph.aggregate_total == 3
    assert graph.import_edge_total == 6
    assert graph.importing_file_total == 4
    # Every community carries exactly one edge to the package it imports.
    assert [(e.source, e.target, e.import_edge_count) for e in graph.edges] == [
        ("community:7", "package:npm:react", 2),
        ("community:3", "package:npm:react", 3),
        ("community:9", "package:npm:react", 1),
    ]


def test_a_community_label_prefers_its_own_name() -> None:
    labelled = json.dumps({"label": "  Web surface  "})
    edges = [
        _Edge("packages/web/a.ts", community_id=1, community_meta_json=labelled),
        _Edge("single.ts", community_id=2, community_meta_json="{}"),
        _Edge("packages/ui/b.ts", community_id=3, community_meta_json=None),
        _Edge("x/y.ts", community_id=4, community_meta_json="not json"),
    ]

    graph = build_relationship_graph(DECLARED, LINKED, edges, "npm:react")

    assert graph is not None
    labels = {n.community_id: n.label for n in graph.nodes}
    assert labels[1] == "Web surface"
    # No label: the shared root of what it imports from, else the bare id.
    assert labels[2] == "single.ts"
    assert labels[3] == "packages/ui"
    # Two segments is already the root; unparseable metadata degrades to it too.
    assert labels[4] == "x/y.ts"


def test_a_community_with_no_label_and_no_files_falls_back_to_its_id() -> None:
    from repowise.core.analysis.external_systems.relationships import _community_label

    assert _community_label(None, 12, None) == "Community 12"
    assert _community_label(json.dumps({"label": "   "}), 12, None) == "Community 12"


def test_the_graph_is_bounded_by_the_smaller_of_its_two_limits() -> None:
    edges = [_Edge(f"src/{index}.ts", community_id=index) for index in range(6)]

    assert _returned(edges, node_limit=2, edge_limit=50) == 2
    assert _returned(edges, node_limit=50, edge_limit=3) == 3
    assert _returned(edges, node_limit=50, edge_limit=50) == 6


def _returned(edges: list[_Edge], *, node_limit: int, edge_limit: int) -> int:
    graph = build_relationship_graph(
        DECLARED, LINKED, edges, "npm:react", node_limit=node_limit, edge_limit=edge_limit
    )
    assert graph is not None
    assert graph.aggregate_returned == graph.edge_returned == len(graph.nodes)
    assert graph.truncated is (graph.aggregate_total > len(graph.nodes))
    return len(graph.nodes)


def test_totals_count_every_community_not_just_the_returned_ones() -> None:
    edges = [_Edge(f"src/{index}.ts", community_id=index) for index in range(6)]

    graph = build_relationship_graph(DECLARED, LINKED, edges, "npm:react", node_limit=2)

    assert graph is not None
    assert graph.aggregate_total == 6
    assert graph.edge_total == 6
    assert graph.import_edge_total == 6
    assert graph.importing_file_total == 6
    assert graph.aggregate_returned == 2
    assert graph.truncated is True


def test_importers_outside_the_scope_are_not_counted() -> None:
    edges = [
        _Edge("src/a.ts", community_id=1),
        _Edge("local-stash/b.ts", community_id=1),
        _Edge(".claude/worktrees/w/c.ts", community_id=1),
    ]

    scoped = build_relationship_graph(DECLARED, LINKED, edges, "npm:react")
    assert scoped is not None
    assert scoped.nodes[0].importing_file_count == 1

    everything = build_relationship_graph(DECLARED, LINKED, edges, "npm:react", scope="all")
    assert everything is not None
    assert everything.nodes[0].importing_file_count == 3


def test_edges_to_other_packages_are_ignored() -> None:
    edges = [_Edge("src/a.ts", target_node_id="external:vue", community_id=1)]

    graph = build_relationship_graph(DECLARED, LINKED, edges, "npm:react")

    assert graph is not None
    assert graph.nodes == []
    assert graph.aggregate_total == 0


def test_file_expansion_pages_one_community() -> None:
    edges = [
        _Edge("src/a.ts", community_id=1),
        _Edge("src/a.ts", community_id=1),
        _Edge("src/b.ts", community_id=1),
        _Edge("other/c.ts", community_id=2),
    ]

    page = build_importing_files(DECLARED, LINKED, edges, "npm:react", "community:1")

    assert page is not None
    assert page.total == 2
    assert [(i.path, i.import_edge_count) for i in page.items] == [("src/a.ts", 2), ("src/b.ts", 1)]
    assert page.items[0].language == "ts"
    assert page.truncated is False
    assert page.limit == DEFAULT_FILE_LIMIT


def test_file_expansion_counts_the_targets_each_file_reached() -> None:
    links = [_Link(node_id="external:react"), _Link(node_id="external:react/jsx-runtime")]
    edges = [
        _Edge("src/a.ts", target_node_id="external:react"),
        _Edge("src/a.ts", target_node_id="external:react/jsx-runtime"),
        _Edge("src/b.ts", target_node_id="external:react"),
    ]

    page = build_importing_files(DECLARED, links, edges, "npm:react", "community:1")

    assert page is not None
    assert [(i.path, i.matched_external_node_count) for i in page.items] == [
        ("src/a.ts", 2),
        ("src/b.ts", 1),
    ]


def test_file_expansion_is_bounded_independently_of_the_graph() -> None:
    edges = [_Edge(f"src/{index:03d}.ts") for index in range(5)]

    first = build_importing_files(DECLARED, LINKED, edges, "npm:react", "community:1", limit=2)
    assert first is not None
    assert [i.path for i in first.items] == ["src/000.ts", "src/001.ts"]
    assert first.total == 5
    assert first.truncated is True

    past_end = build_importing_files(
        DECLARED, LINKED, edges, "npm:react", "community:1", limit=2, offset=99
    )
    assert past_end is not None
    assert past_end.items == []
    assert past_end.total == 5
    assert past_end.truncated is False


def test_file_expansion_of_an_unlinked_package_is_empty_not_missing() -> None:
    page = build_importing_files(DECLARED, [], [], "npm:react", "community:1")

    assert page is not None
    assert page.items == []
    assert page.total == 0
    assert page.truncated is False


def test_the_graph_and_the_expansion_share_one_target_universe() -> None:
    """A community may not claim importers the drill-down cannot show."""
    links = [
        _Link(node_id=f"external:react/m{index:04d}") for index in range(EVIDENCE_TARGET_LIMIT + 5)
    ]
    edges = [
        _Edge(f"src/{index:04d}.ts", target_node_id=link.node_id)
        for index, link in enumerate(links)
    ]

    graph = build_relationship_graph(DECLARED, links, edges, "npm:react")
    page = build_importing_files(
        DECLARED, links, edges, "npm:react", "community:1", limit=EVIDENCE_TARGET_LIMIT + 5
    )

    assert graph is not None and page is not None
    assert graph.nodes[0].importing_file_count == EVIDENCE_TARGET_LIMIT
    assert page.total == EVIDENCE_TARGET_LIMIT


@pytest.mark.parametrize("bad", ["", "react", ":react", "npm:"])
def test_a_malformed_package_key_is_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="ecosystem and package name"):
        split_package_key(bad)
    with pytest.raises(ValueError, match="ecosystem and package name"):
        build_relationship_graph(DECLARED, LINKED, [], bad)


@pytest.mark.parametrize("bad", ["", "community", "community:x", "cluster:1"])
def test_a_malformed_aggregate_key_is_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="graph community"):
        split_community_key(bad)
    with pytest.raises(ValueError, match="graph community"):
        build_importing_files(DECLARED, LINKED, [], "npm:react", bad)


def test_a_scoped_npm_name_round_trips_through_the_key() -> None:
    declared = [_Declaration(name="@scope/pkg")]
    links = [_Link(node_id="external:@scope/pkg", name="@scope/pkg")]

    graph = build_relationship_graph(declared, links, [], "npm:@scope/pkg")

    assert graph is not None
    assert graph.package_name == "@scope/pkg"
    assert graph.match_basis == "exact"


def test_plain_dicts_fold_the_same_as_objects() -> None:
    as_objects = build_relationship_graph(DECLARED, LINKED, [_Edge("src/a.ts")], "npm:react")
    as_dicts = build_relationship_graph(
        [{"ecosystem": "npm", "name": "react", "declared_in": "package.json"}],
        [{"node_id": "external:react", "ecosystem": "npm", "name": "react"}],
        [
            {
                "source_path": "src/a.ts",
                "target_node_id": "external:react",
                "community_id": 1,
                "community_meta_json": "{}",
                "language": "ts",
            }
        ],
        "npm:react",
    )

    assert as_objects is not None and as_dicts is not None
    assert as_dicts.as_dict() == as_objects.as_dict()
