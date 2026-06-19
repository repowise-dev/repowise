"""Tests for cross-repo blast radius — reachability, weighting, ranking, cycles."""

from __future__ import annotations

from repowise.core.workspace.blast_radius import (
    BEHAVIORAL_EDGE_WEIGHT,
    MAX_IMPACTED,
    CrossRepoBlastRadius,
    cross_repo_blast_radius,
    resolve_targets,
)
from repowise.core.workspace.contracts import Contract, ContractLink
from repowise.core.workspace.cross_repo import (
    CrossRepoCoChange,
    CrossRepoOverlay,
    CrossRepoPackageDep,
)
from repowise.core.workspace.extractors.service_boundary import ServiceBoundary
from repowise.core.workspace.system_graph import build_system_graph


# ---------------------------------------------------------------------------
# Fixture builders (mirror test_system_graph.py)
# ---------------------------------------------------------------------------


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


def _link(cid, p_repo, p_file, c_repo, c_file, ctype="http", match="exact", conf=0.9) -> ContractLink:
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


def _cochange(s_repo, s_file, t_repo, t_file, strength=0.7) -> CrossRepoCoChange:
    return CrossRepoCoChange(
        source_repo=s_repo,
        source_file=s_file,
        target_repo=t_repo,
        target_file=t_file,
        strength=strength,
        frequency=4,
        last_date="2026-06-01",
    )


def _chain_graph():
    """web --http--> api --http--> db   (each consumer calls the next provider).

    Edge direction is consumer→provider, so changing ``db`` impacts ``api`` then
    ``web`` walking the reverse links.
    """
    contracts = [
        _provider("db", "http::GET::/rows", file="db/h.py"),
        _consumer("api", "http::GET::/rows", file="api/c.py"),
        _provider("api", "http::GET::/users", file="api/h.py"),
        _consumer("web", "http::GET::/users", file="web/c.py"),
    ]
    links = [
        _link("http::GET::/rows", "db", "db/h.py", "api", "api/c.py"),
        _link("http::GET::/users", "api", "api/h.py", "web", "web/c.py"),
    ]
    return build_system_graph(contracts, links, CrossRepoOverlay(), {})


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


def test_resolve_target_by_node_id():
    graph = _chain_graph()
    resolved, unresolved = resolve_targets(graph, ["api"])
    assert resolved == ["api"]
    assert unresolved == []


def test_resolve_target_by_repo_alias_expands_to_all_nodes():
    boundaries = {
        "mono": [
            ServiceBoundary(service_path="services/auth", service_name="auth"),
            ServiceBoundary(service_path="services/billing", service_name="billing"),
        ]
    }
    contracts = [
        _provider("mono", "http::a", file="services/auth/h.py"),
        _provider("mono", "http::b", file="services/billing/h.py"),
    ]
    graph = build_system_graph(contracts, [], CrossRepoOverlay(), boundaries)
    resolved, unresolved = resolve_targets(graph, ["mono"])
    assert set(resolved) == {"mono::services/auth", "mono::services/billing"}
    assert unresolved == []


def test_resolve_unknown_target():
    graph = _chain_graph()
    resolved, unresolved = resolve_targets(graph, ["ghost"])
    assert resolved == []
    assert unresolved == ["ghost"]


# ---------------------------------------------------------------------------
# Reachability + direction
# ---------------------------------------------------------------------------


def test_changing_provider_reaches_transitive_consumers():
    graph = _chain_graph()
    result = cross_repo_blast_radius(graph, ["db"])
    impacted = {n.id: n for n in result.impacted}
    # db's direct dependent is api (distance 1); web is transitive (distance 2).
    assert set(impacted) == {"api", "web"}
    assert impacted["api"].distance == 1
    assert impacted["web"].distance == 2
    assert result.max_distance == 2


def test_leaf_consumer_has_no_downstream():
    graph = _chain_graph()
    result = cross_repo_blast_radius(graph, ["web"])
    # Nothing depends on web (it only consumes), so nothing is impacted.
    assert result.impacted == []
    assert result.total_impacted == 0


def test_distance_decay_ranks_nearer_higher():
    graph = _chain_graph()
    result = cross_repo_blast_radius(graph, ["db"])
    api = next(n for n in result.impacted if n.id == "api")
    web = next(n for n in result.impacted if n.id == "web")
    assert api.score > web.score
    # impacted list is sorted by score desc → api first.
    assert result.impacted[0].id == "api"


def test_max_depth_bounds_traversal():
    graph = _chain_graph()
    result = cross_repo_blast_radius(graph, ["db"], max_depth=1)
    assert {n.id for n in result.impacted} == {"api"}
    assert result.max_distance == 1


# ---------------------------------------------------------------------------
# Structural vs behavioral weighting + labeling
# ---------------------------------------------------------------------------


def test_structural_and_behavioral_are_labeled_distinctly():
    contracts = [
        _provider("api", "http::GET::/users", file="api/h.py"),
        _consumer("web", "http::GET::/users", file="web/c.py"),
    ]
    links = [_link("http::GET::/users", "api", "api/h.py", "web", "web/c.py")]
    overlay = CrossRepoOverlay(
        co_changes=[_cochange("api", "api/h.py", "infra", "infra/deploy.yaml", strength=0.7)]
    )
    graph = build_system_graph(contracts, links, overlay, {})
    result = cross_repo_blast_radius(graph, ["api"])
    by_id = {n.id: n for n in result.impacted}
    # web depends on api via http (structural); infra only co-changes (behavioral).
    assert by_id["web"].structural is True
    assert by_id["infra"].structural is False
    assert result.structural_count == 1
    assert result.behavioral_count == 1


def test_behavioral_edge_scores_lower_than_structural_at_same_distance():
    # api has one structural dependent (web) and one co-change partner (infra),
    # both at distance 1, both with the same edge confidence — the behavioral
    # one must rank lower purely from BEHAVIORAL_EDGE_WEIGHT.
    contracts = [
        _provider("api", "http::GET::/u", file="api/h.py"),
        _consumer("web", "http::GET::/u", file="web/c.py", ),
    ]
    links = [_link("http::GET::/u", "api", "api/h.py", "web", "web/c.py", conf=0.7)]
    overlay = CrossRepoOverlay(
        co_changes=[_cochange("api", "api/h.py", "infra", "infra/x.yaml", strength=0.7)]
    )
    graph = build_system_graph(contracts, links, overlay, {})
    result = cross_repo_blast_radius(graph, ["api"])
    by_id = {n.id: n for n in result.impacted}
    assert by_id["web"].score > by_id["infra"].score
    # The behavioral score is exactly the structural one scaled by the weight.
    assert by_id["infra"].score == round(by_id["web"].score * BEHAVIORAL_EDGE_WEIGHT, 4)


def test_include_behavioral_false_drops_co_change_edges():
    contracts = [
        _provider("api", "http::GET::/u", file="api/h.py"),
        _consumer("web", "http::GET::/u", file="web/c.py"),
    ]
    links = [_link("http::GET::/u", "api", "api/h.py", "web", "web/c.py")]
    overlay = CrossRepoOverlay(
        co_changes=[_cochange("api", "api/h.py", "infra", "infra/x.yaml")]
    )
    graph = build_system_graph(contracts, links, overlay, {})
    result = cross_repo_blast_radius(graph, ["api"], include_behavioral=False)
    assert {n.id for n in result.impacted} == {"web"}
    assert result.behavioral_count == 0


def test_co_change_propagates_both_directions():
    overlay = CrossRepoOverlay(
        co_changes=[_cochange("a", "a/x.py", "b", "b/y.py", strength=0.8)]
    )
    graph = build_system_graph([], [], overlay, {})
    # Changing either side impacts the other (undirected behavioral edge).
    assert {n.id for n in cross_repo_blast_radius(graph, ["a"]).impacted} == {"b"}
    assert {n.id for n in cross_repo_blast_radius(graph, ["b"]).impacted} == {"a"}


def test_package_dep_impact_points_to_dependents():
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
    # web depends on shared → changing shared impacts web.
    result = cross_repo_blast_radius(graph, ["shared"])
    assert {n.id for n in result.impacted} == {"web"}
    assert result.impacted[0].structural is True
    assert result.impacted[0].edge_kinds == ["package"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_unknown_target_yields_empty_with_unresolved():
    graph = _chain_graph()
    result = cross_repo_blast_radius(graph, ["ghost"])
    assert result.impacted == []
    assert result.unresolved_targets == ["ghost"]
    assert result.targets == []


def test_cycle_is_traversed_safely():
    # a ↔ b structural cycle: a depends on b AND b depends on a.
    contracts = [
        _provider("a", "http::pa", file="a/h.py"),
        _consumer("b", "http::pa", file="b/c.py"),
        _provider("b", "http::pb", file="b/h.py"),
        _consumer("a", "http::pb", file="a/c.py"),
    ]
    links = [
        _link("http::pa", "a", "a/h.py", "b", "b/c.py"),
        _link("http::pb", "b", "b/h.py", "a", "a/c.py"),
    ]
    graph = build_system_graph(contracts, links, CrossRepoOverlay(), {})
    result = cross_repo_blast_radius(graph, ["a"])
    # b is impacted; a (the target) is never re-impacted onto itself.
    assert {n.id for n in result.impacted} == {"b"}


def test_impacted_repos_excludes_target_repo():
    graph = _chain_graph()
    result = cross_repo_blast_radius(graph, ["db"])
    assert "db" not in result.impacted_repos
    assert set(result.impacted_repos) == {"api", "web"}


def test_result_is_capped_but_total_is_honest():
    # One provider with many distinct consumers → many distance-1 impacted nodes.
    contracts = [_provider("hub", "http::GET::/x", file="hub/h.py")]
    links = []
    for i in range(MAX_IMPACTED + 20):
        repo = f"c{i}"
        contracts.append(_consumer(repo, "http::GET::/x", file=f"{repo}/c.py"))
        links.append(_link("http::GET::/x", "hub", "hub/h.py", repo, f"{repo}/c.py"))
    graph = build_system_graph(contracts, links, CrossRepoOverlay(), {})
    result = cross_repo_blast_radius(graph, ["hub"])
    assert len(result.impacted) == MAX_IMPACTED
    assert result.total_impacted == MAX_IMPACTED + 20


def test_to_dict_shape_is_locked():
    graph = _chain_graph()
    data = cross_repo_blast_radius(graph, ["db"]).to_dict()
    assert set(data) == {
        "targets",
        "target_repos",
        "impacted",
        "impacted_repos",
        "structural_count",
        "behavioral_count",
        "max_distance",
        "total_impacted",
        "unresolved_targets",
    }
    assert set(data["impacted"][0]) == {
        "id",
        "repo",
        "name",
        "kind",
        "distance",
        "score",
        "structural",
        "edge_kinds",
    }


def test_empty_graph_is_safe():
    graph = build_system_graph([], [], CrossRepoOverlay(), {})
    result = cross_repo_blast_radius(graph, ["anything"])
    assert isinstance(result, CrossRepoBlastRadius)
    assert result.impacted == []
    assert result.total_impacted == 0
