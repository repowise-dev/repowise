"""Links between two services, read from the stored artifacts.

``eshop.json`` is cut from a real index of the eShop sample: every node of its
system graph, the contract links, package dependencies and co-changes touching
webapp or ordering.api, the contracts at their ends and the service links.
"""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.workspace.contracts import Contract, ContractLink
from repowise.core.workspace.cross_repo import CrossRepoCoChange, CrossRepoOverlay
from repowise.core.workspace.extractors.service_boundary import ServiceBoundary
from repowise.core.workspace.links_between import (
    A_TO_B,
    B_TO_A,
    BOTH,
    build_links_index,
    links_between,
)
from repowise.core.workspace.manifests import CrossRepoPackageDep
from repowise.core.workspace.system_graph import build_system_graph

FIXTURE = Path(__file__).parents[2] / "fixtures" / "workspace_links" / "eshop.json"


def _eshop_index(**graph_extra):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    graph = {"nodes": data["nodes"], "repo_provenance": data["repo_provenance"], **graph_extra}
    return build_links_index(graph, data, data)


def _eshop(**kwargs):
    return links_between(_eshop_index(), **kwargs)


def test_webapp_and_ordering_api_share_eight_links_all_one_way():
    result = _eshop(a="webapp", b="ordering.api")
    assert result["total"] == 8
    assert result["stale_graph"] is False
    assert result["totals"] == [
        {"kind": "event", "direction": A_TO_B, "count": 6},
        {"kind": "http", "direction": A_TO_B, "count": 2},
    ]
    assert {row["direction"] for row in result["links"]} == {A_TO_B}
    http = [row["link"] for row in result["links"] if row["kind"] == "http"]
    assert [lk["contract_id"] for lk in http] == [
        "http::GET::/api/orders",
        "http::POST::/api/orders",
    ]
    assert {lk["strategy"] for lk in http} == {"service_mapped"}


def test_swapping_the_pair_flips_the_direction_only():
    result = _eshop(a="ordering.api", b="webapp")
    assert result["total"] == 8
    assert {row["direction"] for row in result["links"]} == {B_TO_A}


def test_rows_carry_both_ends_origin():
    row = _eshop(a="webapp", b="ordering.api", kind="event")["links"][0]
    assert row["evidence"] == "contract"
    assert row["provider_origin"] == "dataflow"
    assert row["consumer_origin"] == "client_call"


def test_kind_filters_rows_but_keeps_every_kind_counted():
    result = _eshop(a="webapp", b="ordering.api", kind="http")
    assert result["total"] == 2
    assert {row["kind"] for row in result["links"]} == {"http"}
    assert sum(t["count"] for t in result["totals"]) == 8


def test_q_narrows_rows_and_totals():
    result = _eshop(a="webapp", b="ordering.api", q="orderstatuschangedtopaid")
    assert result["total"] == 1
    assert result["totals"] == [{"kind": "event", "direction": A_TO_B, "count": 1}]


def test_pages_are_cut_from_the_ordered_rows():
    index = _eshop_index()
    whole = links_between(index, a="webapp", b="ordering.api")["links"]
    page = links_between(index, a="webapp", b="ordering.api", limit=3, offset=6)
    assert page["total"] == 8
    assert page["links"] == whole[6:9]


def test_a_gateway_route_counts_as_the_graph_counts_it():
    result = _eshop(a="webapp", b="catalog.api")
    assert sorted(row["link"]["strategy"] for row in result["links"]) == [
        "gateway_route",
        "service_mapped",
    ]


def test_a_build_edge_lists_its_manifest_dependency():
    result = _eshop(a="webapp", b="eshop.servicedefaults")
    assert result["totals"] == [{"kind": "package", "direction": A_TO_B, "count": 1}]
    row = result["links"][0]
    assert row["evidence"] == "package"
    assert row["link"] is None
    assert row["package_dep"]["kind"] == "dotnet_project_ref"
    assert _eshop(a="webapp", b="eshop.servicedefaults", q="nothing-matches")["total"] == 0


def test_graph_from_another_build_is_flagged_stale():
    index = _eshop_index(repo_provenance={"webapp": {"head": "other"}})
    assert links_between(index, a="webapp", b="ordering.api")["stale_graph"] is True


def test_unknown_service_is_none():
    assert _eshop(a="webapp", b="nope") is None


def _link(cid, consumer_file, provider_file):
    return ContractLink(
        contract_id=cid,
        contract_type="http",
        match_type="exact",
        confidence=0.8,
        provider_repo="shop",
        provider_file=provider_file,
        provider_symbol=cid,
        provider_service=None,
        consumer_repo="shop",
        consumer_file=consumer_file,
        consumer_symbol=cid,
        consumer_service=None,
    )


def _monorepo():
    boundaries = {
        "shop": [
            ServiceBoundary(service_path="services/orders", service_name="orders"),
            ServiceBoundary(service_path="services/orders/worker", service_name="worker"),
            ServiceBoundary(service_path="services/cart", service_name="cart"),
        ]
    }
    links = [
        _link("http::GET::/orders", "services/cart/api.py", "services/orders/routes.py"),
        _link("http::POST::/orders", "services/cart/api.py", "services/orders/routes.py"),
        _link("http::GET::/carts", "services/orders/client.py", "services/cart/routes.py"),
        # The deeper boundary is its own service, not orders.
        _link("http::GET::/jobs", "services/cart/api.py", "services/orders/worker/jobs.py"),
        # Same service on both ends: never an edge, never a row.
        _link("http::GET::/self", "services/cart/a.py", "services/cart/b.py"),
    ]
    overlay = CrossRepoOverlay(
        co_changes=[
            CrossRepoCoChange(
                source_repo="shop",
                source_file="services/orders/models.py",
                target_repo="shop",
                target_file="services/cart/models.py",
                strength=0.5,
                frequency=3,
                last_date="2026-10-01",
            )
        ],
        package_deps=[
            CrossRepoPackageDep(
                source_repo="shop",
                target_repo="shop",
                source_manifest="services/cart/pyproject.toml",
                target_manifest="services/orders/pyproject.toml",
                kind="pip_path",
            )
        ],
    )
    contract = Contract(
        repo="shop",
        contract_id="http::GET::/orders",
        contract_type="http",
        role="provider",
        file_path="services/orders/routes.py",
        symbol_name="orders",
        confidence=0.85,
    )
    graph = build_system_graph([contract], links, overlay, boundaries).to_dict()
    store = {"contracts": [contract.to_dict()], "contract_links": [lk.to_dict() for lk in links]}
    return graph, build_links_index(graph, store, overlay.to_dict())


CART, ORDERS = "shop::services/cart", "shop::services/orders"


def test_two_services_in_one_repo_list_every_kind():
    _, index = _monorepo()
    result = links_between(index, a=CART, b=ORDERS)
    assert result["totals"] == [
        {"kind": "co_change", "direction": BOTH, "count": 1},
        {"kind": "http", "direction": A_TO_B, "count": 2},
        {"kind": "http", "direction": B_TO_A, "count": 1},
        {"kind": "package", "direction": A_TO_B, "count": 1},
    ]
    co_change = next(row for row in result["links"] if row["evidence"] == "co_change")
    assert co_change["co_change"]["frequency"] == 3
    assert links_between(index, a=CART, b=CART)["total"] == 0


def test_a_pairs_rows_add_up_to_its_edge_weights():
    graph, index = _monorepo()
    edges = [e for e in graph["edges"] if {e["source"], e["target"]} == {CART, ORDERS}]
    assert {e["kind"] for e in edges} == {"http", "package", "co_change"}
    for edge in edges:
        result = links_between(index, a=edge["source"], b=edge["target"], kind=edge["kind"])
        direction = BOTH if edge["kind"] == "co_change" else A_TO_B
        count = sum(
            t["count"]
            for t in result["totals"]
            if t["kind"] == edge["kind"] and t["direction"] == direction
        )
        assert count == edge["weight"], edge["id"]
