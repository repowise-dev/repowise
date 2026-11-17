"""Tests for service grouping: tag, name family and linkage sources, the size gate, determinism."""

from __future__ import annotations

import random

from repowise.core.workspace import grouping
from repowise.core.workspace.contracts import Contract
from repowise.core.workspace.cross_repo import CrossRepoOverlay
from repowise.core.workspace.grouping import MIN_SERVICES_TO_GROUP, group_services
from repowise.core.workspace.system_graph import (
    SystemEdge,
    SystemGraph,
    SystemNode,
    build_system_graph,
)


def _node(repo: str) -> SystemNode:
    return SystemNode(id=repo, repo=repo, service_path=None, name=repo)


def _edge(source: str, target: str, kind: str = "http", weight: int = 1) -> SystemEdge:
    return SystemEdge(
        id=f"{source}->{target}:{kind}",
        source=source,
        target=target,
        kind=kind,
        match_type="exact",
        confidence=0.9,
        weight=weight,
        structural=kind != "co_change",
    )


def _filler(count: int, prefix: str = "solo") -> list[SystemNode]:
    """Services that share no name and no link: they group under no source."""
    return [_node(f"{prefix}{i:03d}") for i in range(count)]


def _family(name: str, size: int, sep: str = "-") -> list[SystemNode]:
    return [_node(f"{name}{sep}svc{i}") for i in range(size)]


# ---------------------------------------------------------------------------
# Size gate
# ---------------------------------------------------------------------------


def test_estate_at_the_threshold_is_never_grouped():
    nodes = _family("ordering", 5) + _family("catalog", 5) + _filler(MIN_SERVICES_TO_GROUP - 10)
    assert len(nodes) == MIN_SERVICES_TO_GROUP
    assert group_services(nodes, []) == ({}, [])


def test_one_group_is_not_enough():
    nodes = _family("ordering", 5) + _filler(50)
    assert group_services(nodes, []) == ({}, [])


def test_eshop_sized_estate_stays_flat():
    # 18 services in 18 repos, name families and runtime links present.
    nodes = (
        _family("ordering", 4, ".")
        + _family("catalog", 3, ".")
        + _family("basket", 2, ".")
        + _filler(9)
    )
    edges = [_edge("ordering.svc0", "catalog.svc0"), _edge("basket.svc0", "catalog.svc1")]
    assert group_services(nodes, edges, {"ordering.svc0": ["group:ordering"]}) == ({}, [])


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def test_only_a_group_tag_declares_a_group():
    nodes = [_node(a) for a in ("api-a", "api-b", "web-a", "web-b", "job", "cron")]
    nodes += _filler(40)
    tags = {
        "api-a": ["edge", "group:checkout"],
        "web-a": ["group:checkout"],
        "api-b": ["backend", "Group: Billing"],
        "web-b": ["group:Billing", "legacy"],
        # Conformance tags shared by two repos never make a group.
        "job": ["backend", "python"],
        "cron": ["backend", "python"],
    }
    assignment, groups = group_services(nodes, [], tags)
    assert assignment == {
        "api-a": "tag:checkout",
        "web-a": "tag:checkout",
        "api-b": "tag:Billing",
        "web-b": "tag:Billing",
    }
    assert {(g.id, g.label, g.source, g.member_count) for g in groups} == {
        ("tag:checkout", "checkout", "tag", 2),
        ("tag:Billing", "Billing", "tag", 2),
    }


def test_declared_name_reads_only_group_tags():
    assert grouping.declared_group(["group:", " web "]) is None
    assert grouping.declared_group(["backend"]) is None
    assert grouping.declared_group(["group:", "group: core "]) == "core"
    assert grouping.declared_group(["edge", "Group: Checkout"]) == "Checkout"
    assert grouping.declared_group([]) is None


def test_lone_tag_falls_through_to_name_family():
    nodes = _family("ordering", 3) + _family("catalog", 3) + _filler(40)
    assignment, _ = group_services(nodes, [], {"ordering-svc0": ["group:solo"]})
    assert assignment["ordering-svc0"] == "name:ordering"


def test_name_family_needs_two_repos_and_under_half_the_estate():
    nodes = _family("ordering", 3) + _family("catalog", 2) + _filler(40)
    # A monorepo's services share one repo: never a family on their own.
    nodes += [
        SystemNode(id=f"mono::svc{i}", repo="mono-repo", service_path=f"svc{i}", name=f"svc{i}")
        for i in range(3)
    ]
    assignment, groups = group_services(nodes, [])
    assert {g.id for g in groups} == {"name:ordering", "name:catalog"}
    assert not any(nid.startswith("mono::") for nid in assignment)
    assert not any(nid.startswith("solo") for nid in assignment)


def test_bare_alias_joins_the_family_its_name_starts():
    nodes = [_node("ordering"), *_family("ordering", 2, "."), *_family("catalog", 2), *_filler(40)]
    assignment, _ = group_services(nodes, [])
    assert assignment["ordering"] == "name:ordering"


def test_organisation_prefix_splits_on_the_next_token():
    # "acme" covers every repo, so families come from the second token.
    nodes = (
        [_node(f"acme-billing-{i}") for i in range(4)]
        + [_node(f"acme-search-{i}") for i in range(4)]
        + [_node(f"acme-misc{i}") for i in range(40)]
    )
    assignment, groups = group_services(nodes, [])
    assert {(g.id, g.label) for g in groups} == {
        ("name:acme.billing", "acme-billing"),
        ("name:acme.search", "acme-search"),
    }
    assert "acme-misc0" not in assignment


def test_linkage_clusters_ungrouped_services_on_runtime_edges():
    left = [_node(f"l{i}") for i in range(4)]
    right = [_node(f"r{i}") for i in range(4)]
    nodes = left + right + _filler(40)
    edges = [_edge(f"l{i}", "l0") for i in range(1, 4)] + [_edge("l1", "l2", "event")]
    edges += [_edge(f"r{i}", "r3", "grpc") for i in range(3)] + [_edge("r0", "r1", "db")]
    # Build and history edges never pull services together.
    edges += [_edge("l1", "r1", "package"), _edge("l2", "r2", "co_change")]
    edges += [_edge(f"solo{i:03d}", "l0", "package") for i in range(40)]
    assignment, groups = group_services(nodes, edges)
    assert {(g.id, g.label, g.source, g.member_count) for g in groups} == {
        ("linked:l0", "l0", "linked", 4),
        ("linked:r0", "r3", "linked", 4),
    }
    assert all(assignment[n.id] == "linked:l0" for n in left)
    assert not any(nid.startswith("solo") for nid in assignment)


def test_linked_id_survives_a_new_caller():
    def linked(extra: list[SystemEdge]) -> list[tuple[str, str]]:
        nodes = _family("ordering", 3) + [_node(f"x{c}") for c in "abcd"] + _filler(40)
        pairs = ("ad", "bd", "cd", "ab", "bc", "ac")
        edges = [_edge(f"x{s}", f"x{t}") for s, t in pairs] + extra
        return [(g.id, g.label) for g in group_services(nodes, edges)[1] if g.source == "linked"]

    # The id names the smallest member; the label the most-called one.
    assert linked([]) == [("linked:xa", "xd")]
    # xc gains a caller and ties xd: the label moves, the id does not.
    assert linked([_edge("xd", "xc")]) == [("linked:xa", "xc")]


def test_unknown_edge_kind_is_history_and_never_links():
    nodes = _family("ordering", 3) + [_node(f"x{i}") for i in range(3)] + _filler(40)
    edges = [_edge("x1", "x0", "carrier_pigeon"), _edge("x2", "x0", "carrier_pigeon")]
    assert edges[0].family == "history"
    assert group_services(nodes, edges) == ({}, [])


def test_linkage_skips_services_already_grouped():
    nodes = _family("ordering", 3) + [_node(f"x{i}") for i in range(3)] + _filler(40)
    edges = [_edge("x1", "x0"), _edge("x2", "x0"), _edge("ordering-svc0", "x0")]
    assignment, _ = group_services(nodes, edges)
    assert assignment["ordering-svc0"] == "name:ordering"
    assert {assignment[f"x{i}"] for i in range(3)} == {"linked:x0"}


def test_linkage_past_the_pair_cap_is_skipped(monkeypatch):
    monkeypatch.setattr(grouping, "MAX_LINKAGE_PAIRS", 2)
    nodes = _family("ordering", 3) + [_node(f"x{i}") for i in range(4)] + _filler(40)
    edges = [_edge("x1", "x0"), _edge("x2", "x0"), _edge("x3", "x0")]
    assert group_services(nodes, edges) == ({}, [])


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def _mixed_estate() -> tuple[list[SystemNode], list[SystemEdge], dict[str, list[str]]]:
    nodes = _family("ordering", 4) + _family("catalog", 3) + _filler(60)
    edges = [
        _edge(f"solo{i:03d}", f"solo{(i * 7 + 3) % 60:03d}", "http", weight=i % 4 + 1)
        for i in range(60)
    ]
    tags = {"solo001": ["group:core"], "solo002": ["team:x", "group:core"]}
    return nodes, edges, tags


def test_same_estate_in_any_order_groups_the_same():
    nodes, edges, tags = _mixed_estate()
    expected = group_services(nodes, edges, tags)
    assert expected[1], "the fixture must form groups"
    rng = random.Random(7)
    for _ in range(5):
        shuffled_nodes, shuffled_edges = nodes[:], edges[:]
        rng.shuffle(shuffled_nodes)
        rng.shuffle(shuffled_edges)
        assert group_services(shuffled_nodes, shuffled_edges, tags) == expected


# ---------------------------------------------------------------------------
# Through the system graph
# ---------------------------------------------------------------------------


def _provider(repo: str) -> Contract:
    return Contract(
        repo=repo,
        contract_id=f"http::GET::/{repo}",
        contract_type="http",
        role="provider",
        file_path="src/h.py",
        symbol_name="h",
        confidence=0.9,
    )


def test_synthetic_120_service_estate_groups_by_name_family():
    families = ("ordering", "catalog", "basket", "identity", "payments", "shipping")
    repos = [f"{fam}-{part}" for fam in families for part in range(20)]
    graph = build_system_graph([_provider(r) for r in repos], [], CrossRepoOverlay(), {})
    assert len(graph.nodes) == 120
    assert [(g.id, g.source, g.member_count) for g in graph.groups] == [
        (f"name:{fam}", "name", 20) for fam in sorted(families)
    ]
    assert all(n.group == f"name:{n.repo.split('-')[0]}" for n in graph.nodes)

    data = graph.to_dict()
    assert data["groups"][0] == {
        "id": "name:basket",
        "label": "basket",
        "source": "name",
        "member_count": 20,
    }
    assert SystemGraph.from_dict(data).to_dict() == data


def test_declared_tags_reach_the_graph_through_build():
    repos = [f"svc{i:03d}" for i in range(45)]
    tags = {r: ["group:front"] for r in repos[:3]} | {r: ["group:back"] for r in repos[3:6]}
    graph = build_system_graph(
        [_provider(r) for r in repos], [], CrossRepoOverlay(), {}, repo_tags=tags
    )
    assert {g.id for g in graph.groups} == {"tag:front", "tag:back"}
    assert sum(1 for n in graph.nodes if n.group) == 6


def test_flat_graph_serializes_without_group_keys():
    graph = build_system_graph(
        [_provider(f"svc{i}") for i in range(10)], [], CrossRepoOverlay(), {}
    )
    data = graph.to_dict()
    assert "groups" not in data
    assert all("group" not in n for n in data["nodes"])
