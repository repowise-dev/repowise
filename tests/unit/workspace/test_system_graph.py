"""Tests for the service-granular system graph — node derivation, typed edges, persistence."""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.contracts import Contract, ContractLink, ContractStore
from repowise.core.workspace.cross_repo import (
    CrossRepoCoChange,
    CrossRepoOverlay,
    CrossRepoPackageDep,
)
from repowise.core.workspace.extractors.service_boundary import ServiceBoundary
from repowise.core.workspace.system_graph import (
    SYSTEM_GRAPH_FILENAME,
    SystemGraph,
    build_system_graph,
    edge_kind_for_contract_type,
    load_system_graph,
    save_system_graph,
)


def _provider(repo, cid, ctype="http", file="src/handler.py") -> Contract:
    return Contract(
        repo=repo,
        contract_id=cid,
        contract_type=ctype,
        role="provider",
        file_path=file,
        symbol_name="handler",
        confidence=0.9,
    )


def _consumer(repo, cid, ctype="http", file="src/client.py") -> Contract:
    return Contract(
        repo=repo,
        contract_id=cid,
        contract_type=ctype,
        role="consumer",
        file_path=file,
        symbol_name="call",
        confidence=0.8,
    )


def _link(
    cid, p_repo, p_file, c_repo, c_file, ctype="http", match="exact", conf=0.72
) -> ContractLink:
    return ContractLink(
        contract_id=cid,
        contract_type=ctype,
        match_type=match,
        confidence=conf,
        provider_repo=p_repo,
        provider_file=p_file,
        provider_symbol="handler",
        provider_service=None,
        consumer_repo=c_repo,
        consumer_file=c_file,
        consumer_symbol="call",
        consumer_service=None,
    )


def _nodes_by_id(graph: SystemGraph):
    return {n.id: n for n in graph.nodes}


def _edges_by_key(graph: SystemGraph):
    return {(e.source, e.target, e.kind): e for e in graph.edges}


# ---------------------------------------------------------------------------
# Node derivation (service granularity — D3)
# ---------------------------------------------------------------------------


def test_empty_graph():
    graph = build_system_graph([], [], CrossRepoOverlay(), {})
    assert graph.nodes == []
    assert graph.edges == []
    assert graph.diagnostics.total_providers == 0


def test_repo_without_boundary_collapses_to_one_node():
    contracts = [_provider("api", "http::GET::/users", file="app/routes.py")]
    graph = build_system_graph(contracts, [], CrossRepoOverlay(), {"api": []})
    nodes = _nodes_by_id(graph)
    assert set(nodes) == {"api"}
    assert nodes["api"].service_path is None
    assert nodes["api"].provider_count == 1
    assert nodes["api"].name == "api"


def test_monorepo_yields_multiple_service_nodes():
    boundaries = {
        "mono": [
            ServiceBoundary(service_path="services/auth", service_name="auth"),
            ServiceBoundary(service_path="services/billing", service_name="billing"),
        ]
    }
    contracts = [
        _provider("mono", "http::GET::/login", file="services/auth/handler.py"),
        _provider("mono", "http::POST::/charge", file="services/billing/handler.py"),
    ]
    graph = build_system_graph(contracts, [], CrossRepoOverlay(), boundaries)
    nodes = _nodes_by_id(graph)
    assert set(nodes) == {"mono::services/auth", "mono::services/billing"}
    assert nodes["mono::services/auth"].name == "auth"
    assert nodes["mono::services/auth"].service_path == "services/auth"
    assert nodes["mono::services/billing"].name == "billing"


# ---------------------------------------------------------------------------
# Edge kind + direction + match_type
# ---------------------------------------------------------------------------


def test_contract_type_to_edge_kind():
    assert edge_kind_for_contract_type("http") == "http"
    assert edge_kind_for_contract_type("grpc") == "grpc"
    assert edge_kind_for_contract_type("topic") == "event"
    assert edge_kind_for_contract_type("unknown") == "unknown"


def test_contract_edge_direction_is_consumer_to_provider():
    contracts = [
        _provider("api", "http::GET::/users", file="api/h.py"),
        _consumer("web", "http::GET::/users", file="web/c.py"),
    ]
    links = [_link("http::GET::/users", "api", "api/h.py", "web", "web/c.py")]
    graph = build_system_graph(contracts, links, CrossRepoOverlay(), {})
    assert len(graph.edges) == 1
    edge = graph.edges[0]
    assert edge.source == "web"  # consumer depends on / calls...
    assert edge.target == "api"  # ...the provider
    assert edge.kind == "http"
    assert edge.match_type == "exact"
    assert edge.structural is True
    assert edge.contract_refs == ["http::GET::/users"]


def test_topic_contract_becomes_event_edge():
    contracts = [
        _provider("a", "topic::orders", ctype="topic", file="a/pub.py"),
        _consumer("b", "topic::orders", ctype="topic", file="b/sub.py"),
    ]
    links = [_link("topic::orders", "a", "a/pub.py", "b", "b/sub.py", ctype="topic")]
    graph = build_system_graph(contracts, links, CrossRepoOverlay(), {})
    assert graph.edges[0].kind == "event"


def test_parallel_links_aggregate_into_one_weighted_edge():
    contracts = [
        _provider("api", "http::GET::/a", file="api/h.py"),
        _provider("api", "http::GET::/b", file="api/h.py"),
        _consumer("web", "http::GET::/a", file="web/c.py"),
        _consumer("web", "http::GET::/b", file="web/c.py"),
    ]
    links = [
        _link("http::GET::/a", "api", "api/h.py", "web", "web/c.py", match="exact", conf=0.9),
        _link("http::GET::/b", "api", "api/h.py", "web", "web/c.py", match="candidate", conf=0.5),
    ]
    graph = build_system_graph(contracts, links, CrossRepoOverlay(), {})
    edges = _edges_by_key(graph)
    edge = edges[("web", "api", "http")]
    assert edge.weight == 2
    assert edge.confidence == 0.9  # max of contributors
    assert edge.match_type == "exact"  # most authoritative wins
    assert set(edge.contract_refs) == {"http::GET::/a", "http::GET::/b"}


# ---------------------------------------------------------------------------
# Package + co-change edges
# ---------------------------------------------------------------------------


def test_package_dep_edge_points_dependent_to_dependency():
    overlay = CrossRepoOverlay(
        package_deps=[
            CrossRepoPackageDep(
                source_repo="web",
                target_repo="shared",
                source_manifest="package.json",
                kind="npm_local_path",
            )
        ]
    )
    graph = build_system_graph([], [], overlay, {})
    edges = _edges_by_key(graph)
    edge = edges[("web", "shared", "package")]
    assert edge.structural is True
    assert edge.match_type == "exact"
    assert edge.confidence == 1.0


def test_cochange_edge_is_behavioral_and_undirected():
    overlay = CrossRepoOverlay(
        co_changes=[
            CrossRepoCoChange(
                source_repo="z",
                source_file="z/a.py",
                target_repo="a",
                target_file="a/b.py",
                strength=0.6,
                frequency=4,
                last_date="2026-06-01",
            )
        ]
    )
    graph = build_system_graph([], [], overlay, {})
    assert len(graph.edges) == 1
    edge = graph.edges[0]
    assert edge.kind == "co_change"
    assert edge.structural is False
    # Direction canonicalized to (min, max) of node ids.
    assert edge.source == "a"
    assert edge.target == "z"


# ---------------------------------------------------------------------------
# Orphan + isolation flags
# ---------------------------------------------------------------------------


def test_orphan_provider_and_isolation_flags():
    contracts = [_provider("api", "http::GET::/unused", file="api/h.py")]
    graph = build_system_graph(contracts, [], CrossRepoOverlay(), {})
    node = _nodes_by_id(graph)["api"]
    assert node.is_orphan_provider is True
    assert node.is_isolated is True


def test_connected_provider_is_not_orphan():
    contracts = [
        _provider("api", "http::GET::/users", file="api/h.py"),
        _consumer("web", "http::GET::/users", file="web/c.py"),
    ]
    links = [_link("http::GET::/users", "api", "api/h.py", "web", "web/c.py")]
    graph = build_system_graph(contracts, links, CrossRepoOverlay(), {})
    nodes = _nodes_by_id(graph)
    assert nodes["api"].is_orphan_provider is False
    assert nodes["api"].is_isolated is False
    assert nodes["web"].is_orphan_consumer is False


# ---------------------------------------------------------------------------
# Schema shape + persistence (snapshot guard)
# ---------------------------------------------------------------------------


def test_system_graph_json_shape_is_locked():
    contracts = [
        _provider("api", "http::GET::/users", file="api/h.py"),
        _consumer("web", "http::GET::/users", file="web/c.py"),
    ]
    links = [_link("http::GET::/users", "api", "api/h.py", "web", "web/c.py")]
    graph = build_system_graph(
        contracts, links, CrossRepoOverlay(), {}, version=1, generated_at="t"
    )
    data = graph.to_dict()

    assert set(data) == {"version", "generated_at", "nodes", "edges", "diagnostics"}
    assert set(data["nodes"][0]) == {
        "id",
        "repo",
        "service_path",
        "name",
        "kind",
        "provider_count",
        "consumer_count",
        "contract_types",
        "is_orphan_provider",
        "is_orphan_consumer",
        "is_isolated",
    }
    assert set(data["edges"][0]) == {
        "id",
        "source",
        "target",
        "kind",
        "match_type",
        "confidence",
        "weight",
        "structural",
        "contract_refs",
    }
    assert set(data["diagnostics"]) == {
        "total_providers",
        "total_consumers",
        "total_links",
        "weak_link_count",
        "repo_breakdown",
        "unmatched_consumers",
        "unmatched_by_reason",
        "orphan_providers",
    }


def test_round_trip_serialization():
    contracts = [_provider("api", "http::GET::/users", file="api/h.py")]
    graph = build_system_graph(contracts, [], CrossRepoOverlay(), {}, generated_at="t")
    restored = SystemGraph.from_dict(graph.to_dict())
    assert restored.to_dict() == graph.to_dict()


def test_save_and_load(tmp_path: Path):
    contracts = [_provider("api", "http::GET::/users", file="api/h.py")]
    graph = build_system_graph(contracts, [], CrossRepoOverlay(), {}, generated_at="t")
    out = save_system_graph(graph, tmp_path)
    assert out.name == SYSTEM_GRAPH_FILENAME

    loaded = load_system_graph(tmp_path)
    assert loaded is not None
    assert loaded.to_dict() == graph.to_dict()


def test_load_missing_returns_none(tmp_path: Path):
    assert load_system_graph(tmp_path) is None
