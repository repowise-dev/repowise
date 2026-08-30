"""Building declaration-to-node links from plain records, with no database.

The folds took links as given; only the persistence layer knew how to build
one. These pin the matcher's precedence and its ambiguity refusal, and prove a
consumer holding nothing but declarations and graph nodes can reach the same
folds the server does.
"""

from __future__ import annotations

from dataclasses import dataclass

from repowise.core.analysis.external_systems import (
    ExternalSystemLink,
    build_declaration_index,
    build_declaration_links,
    build_package_summary,
    declaration_name_candidates,
    resolve_declaration,
)


@dataclass
class _Declaration:
    """Stub mirroring the ExternalSystem columns the matcher reads."""

    name: str
    ecosystem: str = "npm"
    declared_in: str = "package.json"


@dataclass
class _Node:
    """Stub mirroring the GraphNode columns the matcher reads."""

    node_id: str


def _links(declarations, nodes) -> list[tuple[str, str, str]]:
    return [(link.node_id, link.ecosystem, link.name) for link in build_declaration_links(declarations, nodes)]


# --- candidate precedence -------------------------------------------------


def test_candidates_lead_with_the_whole_name() -> None:
    assert declaration_name_candidates("react")[0] == "react"


def test_scoped_npm_subpath_falls_back_to_the_package() -> None:
    assert declaration_name_candidates("@scope/pkg/sub") == ("@scope/pkg/sub", "@scope/pkg", "@scope")


def test_rust_and_python_paths_fall_back_to_their_root() -> None:
    assert declaration_name_candidates("serde::de") == ("serde::de", "serde")
    assert declaration_name_candidates("pkg.sub.mod") == ("pkg.sub.mod", "pkg")


def test_candidates_are_deduplicated_and_ordered() -> None:
    assert declaration_name_candidates("a/b") == ("a/b", "a")


# --- resolution -----------------------------------------------------------


def test_exact_and_prefix_nodes_resolve_to_the_declaration() -> None:
    declarations = [_Declaration("react"), _Declaration("serde", ecosystem="cargo")]
    nodes = [_Node("external:react"), _Node("external:react/jsx-runtime"), _Node("external:serde::de")]

    assert _links(declarations, nodes) == [
        ("external:react", "npm", "react"),
        ("external:react/jsx-runtime", "npm", "react"),
        ("external:serde::de", "cargo", "serde"),
    ]


def test_a_name_two_ecosystems_declare_stays_unlinked() -> None:
    declarations = [_Declaration("dup", ecosystem="npm"), _Declaration("dup", ecosystem="pypi")]

    assert _links(declarations, [_Node("external:dup")]) == []


def test_an_ecosystem_qualified_node_resolves_through_the_ambiguity() -> None:
    declarations = [_Declaration("dup", ecosystem="npm"), _Declaration("dup", ecosystem="pypi")]

    assert _links(declarations, [_Node("external:npm:dup")]) == [("external:npm:dup", "npm", "dup")]


def test_an_ambiguous_candidate_refuses_rather_than_falling_through() -> None:
    # The generic separators split at the *first* occurrence, so the only
    # candidates for ``a/b/c`` are itself and ``a``. ``a`` is ambiguous, and
    # matching it returns the refusal instead of resolving the collision.
    declarations = [
        _Declaration("a", ecosystem="npm"),
        _Declaration("a", ecosystem="pypi"),
        _Declaration("a/b/c", ecosystem="npm"),
    ]

    assert _links(declarations, [_Node("external:a/b/c")]) == [("external:a/b/c", "npm", "a/b/c")]
    assert _links(declarations, [_Node("external:a/z")]) == []


def test_only_a_scoped_npm_name_keeps_a_two_segment_fallback() -> None:
    declarations = [_Declaration("@scope/pkg")]

    assert _links(declarations, [_Node("external:@scope/pkg/sub")]) == [
        ("external:@scope/pkg/sub", "npm", "@scope/pkg")
    ]


def test_undeclared_and_non_external_nodes_are_omitted() -> None:
    declarations = [_Declaration("react")]
    nodes = [_Node("external:unknown"), _Node("file:src/index.ts"), _Node("")]

    assert _links(declarations, nodes) == []


def test_node_order_is_preserved() -> None:
    declarations = [_Declaration("react"), _Declaration("vue")]
    nodes = [_Node("external:vue"), _Node("external:react")]

    assert [node_id for node_id, _, _ in _links(declarations, nodes)] == [
        "external:vue",
        "external:react",
    ]


def test_a_declaration_without_a_name_is_skipped() -> None:
    assert build_declaration_index([_Declaration("")]) == {}


def test_resolve_declaration_reports_the_identity_not_a_link() -> None:
    index = build_declaration_index([_Declaration("react")])

    assert resolve_declaration("external:react", index) == ("npm", "react")
    assert resolve_declaration("file:x", index) is None


# --- record shapes --------------------------------------------------------


def test_plain_dicts_link_the_same_as_objects() -> None:
    declarations = [_Declaration("react"), _Declaration("serde", ecosystem="cargo")]
    nodes = [_Node("external:react/jsx-runtime"), _Node("external:serde::de")]
    as_dicts = [
        {"name": d.name, "ecosystem": d.ecosystem, "declared_in": d.declared_in} for d in declarations
    ]

    assert build_declaration_links(as_dicts, [{"node_id": n.node_id} for n in nodes]) == (
        build_declaration_links(declarations, nodes)
    )


def test_links_are_the_record_shape_the_folds_consume() -> None:
    link = ExternalSystemLink(node_id="external:react", ecosystem="npm", name="react")

    assert link.as_dict() == {"node_id": "external:react", "ecosystem": "npm", "name": "react"}


def test_a_consumer_folds_a_summary_from_declarations_and_nodes_alone() -> None:
    """The seam's point: no session, no link table, same fold the server runs."""
    declarations = [_Declaration("react"), _Declaration("jest")]
    nodes = [_Node("external:react"), _Node("external:react/jsx-runtime")]
    edges = [
        {"source_path": "src/app.tsx", "target_node_id": "external:react"},
        {"source_path": "src/ui.tsx", "target_node_id": "external:react/jsx-runtime"},
    ]

    summary = build_package_summary(declarations, build_declaration_links(declarations, nodes), edges)

    assert summary.total_packages == 2
    react = next(entry for entry in summary.items if entry.name == "react")
    assert react.importing_file_count == 2
