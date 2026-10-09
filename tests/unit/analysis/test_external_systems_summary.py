"""The package summary fold: grouping, scope, bounds and the flags that confess them."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from repowise.core.analysis.external_systems import (
    DEFAULT_SUMMARY_LIMIT,
    SUMMARY_VERSION_LIMIT,
    build_package_summary,
    build_registry,
)


@dataclass
class _Declaration:
    """Stub mirroring the ExternalSystem columns the fold reads."""

    ecosystem: str = "npm"
    name: str = "react"
    display_name: str = ""
    category: str = "library"
    io_kind: str | None = None
    version: str | None = None
    declared_in: str = "package.json"
    is_dev_dep: bool = False


@dataclass
class _Link:
    node_id: str
    ecosystem: str = "npm"
    name: str = "react"


@dataclass
class _Edge:
    source_path: str
    target_node_id: str


def _declared(name: str, **kwargs) -> _Declaration:
    return _Declaration(name=name, **kwargs)


def test_groups_declarations_into_one_entry_per_package() -> None:
    summary = build_package_summary(
        [
            _declared("react", declared_in="a/package.json", version="18.0.0"),
            _declared("react", declared_in="b/package.json", version="18.0.0"),
            _declared("jest", declared_in="a/package.json", is_dev_dep=True),
        ],
        [],
        [],
    )

    assert summary.total_packages == 2
    assert summary.total_declarations == 3
    assert summary.manifest_count == 2
    assert summary.ecosystems == ["npm"]
    react = next(item for item in summary.items if item.name == "react")
    assert react.package_key == "npm:react"
    assert react.declaration_count == 2
    assert react.manifest_count == 2


def test_auxiliary_declarations_are_excluded_and_counted() -> None:
    rows = [
        _declared("react", declared_in="package.json"),
        _declared("vue", declared_in="local-stash/package.json"),
        _declared("svelte", declared_in=".claude/worktrees/x/package.json"),
    ]

    primary = build_package_summary(rows, [], [])
    assert [item.name for item in primary.items] == ["react"]
    assert primary.excluded_declarations == 2

    everything = build_package_summary(rows, [], [], scope="all")
    assert sorted(item.name for item in everything.items) == ["react", "svelte", "vue"]
    # Nothing is hidden under this scope, so it makes no claim about omissions.
    assert everything.excluded_declarations == 0


def test_auxiliary_only_repository_still_reports_what_it_hid() -> None:
    summary = build_package_summary([_declared("vue", declared_in="local-stash/p.json")], [], [])

    assert summary.total_packages == 0
    assert summary.items == []
    assert summary.excluded_declarations == 1
    assert summary.manifest_count == 0
    assert summary.truncated is False


def test_versions_are_distinct_sorted_and_capped() -> None:
    versions = [f"1.0.{index}" for index in range(SUMMARY_VERSION_LIMIT + 3)]
    rows = [
        _declared("react", declared_in=f"p{index}/package.json", version=version)
        for index, version in enumerate(versions)
    ]
    rows.append(_declared("react", declared_in="dup/package.json", version=versions[0]))

    entry = build_package_summary(rows, [], []).items[0]

    assert entry.versions == sorted(versions)[:SUMMARY_VERSION_LIMIT]
    assert entry.versions_total == len(versions)
    assert entry.versions_truncated is True
    assert entry.multiple_versions is True


def test_single_version_is_not_truncated_or_multiple() -> None:
    entry = build_package_summary([_declared("react", version="18.0.0")], [], []).items[0]

    assert entry.versions == ["18.0.0"]
    assert entry.versions_total == 1
    assert entry.versions_truncated is False
    assert entry.multiple_versions is False


def test_declarations_without_a_version_contribute_none() -> None:
    entry = build_package_summary([_declared("react", version=None)], [], []).items[0]

    assert entry.versions == []
    assert entry.versions_total == 0
    assert entry.versions_truncated is False


def test_import_evidence_is_scoped_by_importing_file() -> None:
    rows = [_declared("react")]
    links = [_Link(node_id="external:react")]
    edges = [
        _Edge("src/a.ts", "external:react"),
        _Edge("src/a.ts", "external:react"),
        _Edge("src/b.ts", "external:react"),
        _Edge("local-stash/c.ts", "external:react"),
    ]

    primary = build_package_summary(rows, links, edges).items[0]
    assert primary.external_node_count == 1
    assert primary.import_edge_count == 3
    assert primary.importing_file_count == 2
    assert primary.link_state == "linked"

    everything = build_package_summary(rows, links, edges, scope="all").items[0]
    assert everything.import_edge_count == 4
    assert everything.importing_file_count == 3
    # A node exists or it does not; scope is a claim about importers, not nodes.
    assert everything.external_node_count == 1


def test_edges_to_unresolved_targets_are_ignored() -> None:
    entry = build_package_summary(
        [_declared("react")],
        [_Link(node_id="external:react")],
        [_Edge("src/a.ts", "external:something-else")],
    ).items[0]

    assert entry.import_edge_count == 0
    assert entry.link_state == "linked"


def test_declared_but_unlinked_package_reads_unlinked() -> None:
    entry = build_package_summary([_declared("react")], [], []).items[0]

    assert entry.external_node_count == 0
    assert entry.import_edge_count == 0
    assert entry.link_state == "unlinked"


def test_totals_describe_the_whole_scope_not_the_page() -> None:
    rows = [
        _declared("react"),
        _declared("vue"),
        _declared("jest", is_dev_dep=True),
        _declared("react", is_dev_dep=True, declared_in="b/package.json"),
    ]
    links = [_Link(node_id="external:react"), _Link(node_id="external:vue", name="vue")]
    edges = [_Edge("src/a.ts", "external:react")]

    summary = build_package_summary(rows, links, edges, limit=1)

    assert summary.total_packages == 3
    assert summary.returned == 1
    assert summary.runtime_packages == 2
    assert summary.dev_only_packages == 1
    assert summary.observed_packages == 1
    assert summary.linked_packages == 2
    assert summary.unlinked_packages == 1
    # Linked to a node but with no importer in scope — the honest middle state.
    assert summary.linked_without_imports == 1


def test_ordering_puts_runtime_then_most_imported_first() -> None:
    rows = [
        _declared("zzz-dev", is_dev_dep=True),
        _declared("aaa-runtime"),
        _declared("mmm-imported"),
    ]
    links = [_Link(node_id="external:mmm-imported", name="mmm-imported")]
    edges = [_Edge("src/a.ts", "external:mmm-imported")]

    summary = build_package_summary(rows, links, edges)

    assert [item.name for item in summary.items] == ["mmm-imported", "aaa-runtime", "zzz-dev"]


def test_ties_break_on_lowercased_name_then_ecosystem() -> None:
    rows = [
        _declared("Beta"),
        _declared("alpha"),
        _declared("alpha", ecosystem="pypi"),
    ]

    summary = build_package_summary(rows, [], [])

    assert [(i.ecosystem, i.name) for i in summary.items] == [
        ("npm", "alpha"),
        ("pypi", "alpha"),
        ("npm", "Beta"),
    ]


def test_paging_reports_what_it_left_out() -> None:
    rows = [_declared(f"pkg-{index:03d}") for index in range(5)]

    first = build_package_summary(rows, [], [], limit=2)
    assert [i.name for i in first.items] == ["pkg-000", "pkg-001"]
    assert first.returned == 2
    assert first.truncated is True

    last = build_package_summary(rows, [], [], limit=2, offset=4)
    assert [i.name for i in last.items] == ["pkg-004"]
    assert last.truncated is False
    assert last.total_packages == 5

    past_end = build_package_summary(rows, [], [], limit=2, offset=99)
    assert past_end.items == []
    assert past_end.returned == 0
    # Totals still describe the scope even when the page is empty.
    assert past_end.total_packages == 5
    assert past_end.truncated is False


def test_default_limit_is_the_folds_own() -> None:
    rows = [_declared(f"pkg-{index:04d}") for index in range(DEFAULT_SUMMARY_LIMIT + 5)]

    summary = build_package_summary(rows, [], [])

    assert summary.limit == DEFAULT_SUMMARY_LIMIT
    assert summary.returned == DEFAULT_SUMMARY_LIMIT
    assert summary.truncated is True


def test_conflicting_declaration_metadata_resolves_deterministically() -> None:
    rows = [
        _declared("react", category="library", io_kind=None, display_name=""),
        _declared("react", category="framework", io_kind="network", declared_in="b/package.json"),
    ]

    entry = build_package_summary(rows, [], []).items[0]

    # Lowest category and io_kind win, ignoring absent values; display name
    # falls back to the package name when no declaration supplied one.
    assert entry.category == "framework"
    assert entry.io_kind == "network"
    assert entry.display_name == "react"


def test_a_declaration_may_be_both_runtime_and_dev() -> None:
    rows = [
        _declared("react", is_dev_dep=False),
        _declared("react", is_dev_dep=True, declared_in="b/package.json"),
    ]

    entry = build_package_summary(rows, [], []).items[0]

    assert entry.runtime_declared is True
    assert entry.dev_declared is True


def test_empty_input_is_an_honest_empty_page() -> None:
    summary = build_package_summary([], [], [])

    assert summary.as_dict() == {
        "items": [],
        "returned": 0,
        "total_packages": 0,
        "limit": DEFAULT_SUMMARY_LIMIT,
        "offset": 0,
        "truncated": False,
        "scope": "primary",
        "excluded_declarations": 0,
        "total_declarations": 0,
        "runtime_packages": 0,
        "dev_only_packages": 0,
        "observed_packages": 0,
        "linked_packages": 0,
        "unlinked_packages": 0,
        "linked_without_imports": 0,
        "ecosystems": [],
        "manifest_count": 0,
    }


def test_plain_dicts_fold_the_same_as_objects() -> None:
    """The artifact path: records arrive as JSON, not as rows."""
    as_objects = build_package_summary(
        [_declared("react", version="18.0.0")],
        [_Link(node_id="external:react")],
        [_Edge("src/a.ts", "external:react")],
    )
    as_dicts = build_package_summary(
        [
            {
                "ecosystem": "npm",
                "name": "react",
                "display_name": "",
                "category": "library",
                "io_kind": None,
                "version": "18.0.0",
                "declared_in": "package.json",
                "is_dev_dep": False,
            }
        ],
        [{"node_id": "external:react", "ecosystem": "npm", "name": "react"}],
        [{"source_path": "src/a.ts", "target_node_id": "external:react"}],
    )

    assert as_dicts.as_dict() == as_objects.as_dict()


@pytest.mark.parametrize("scope", ["primary", "all"])
def test_scope_is_echoed_back(scope: str) -> None:
    assert build_package_summary([], [], [], scope=scope).scope == scope


def test_registry_keeps_every_declaration_and_orders_by_prominence() -> None:
    rows = [
        _declared("zlib", category="library", declared_in="b/package.json"),
        _declared("fastapi", category="framework"),
        _declared("ruff", category="tool"),
        _declared("postgres", category="service"),
        _declared("zlib", category="library", declared_in="a/package.json"),
        _declared("unknown-kind", category="mystery"),
    ]

    registry = build_registry(rows)

    assert [item.name for item in registry.items] == [
        "fastapi",
        "postgres",
        "ruff",
        "zlib",
        "zlib",
        "unknown-kind",
    ]
    # One row per declaration: the two zlib manifests both survive, in path order.
    assert [i.declared_in for i in registry.items if i.name == "zlib"] == [
        "a/package.json",
        "b/package.json",
    ]
    assert registry.total == 6
    assert registry.manifests == ["a/package.json", "b/package.json", "package.json"]


def test_registry_counts_the_dev_split_and_falls_back_to_the_name() -> None:
    registry = build_registry(
        [
            _declared("react", display_name="React"),
            _declared("jest", display_name="", is_dev_dep=True),
        ]
    )

    assert registry.prod_count == 1
    assert registry.dev_count == 1
    assert {i.name: i.display_name for i in registry.items} == {"react": "React", "jest": "jest"}
