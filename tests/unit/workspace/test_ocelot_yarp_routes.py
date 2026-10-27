"""Ocelot routes, YARP ``ReverseProxy`` config and ``MapForwarder`` in the service-name map."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.workspace.contracts import Contract
from repowise.core.workspace.matching import annotate_consumer_targets, run_matching
from repowise.core.workspace.origins import LinkStrategy
from repowise.core.workspace.service_names import (
    ServiceNameMap,
    build_service_name_map,
    ocelot,
    yarp,
    yarp_forwarder,
)
from repowise.core.workspace.service_names.base import Route, SourceFile, template_upstream

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "service_names"
ESHOPONC = ("web.bff.shopping", "catalog.api", "basket.api", "ordering.api", "ordering.signalrhub", "payment.api")


def _routes(m: ServiceNameMap) -> set[tuple[str, str, str | None, str]]:
    return {(r.gateway, r.prefix, r.upstream, r.repo) for r in m.routes}


def _read(module, text: str, name: str) -> list[tuple[str, Route]]:
    return [(c.name, c.route) for c in module.read(SourceFile(Path(name), text)) if c.route]


def _http(repo: str, role: str, method: str, path: str) -> Contract:
    return Contract(
        repo=repo,
        contract_id=f"http::{method}::{path}",
        contract_type="http",
        role=role,
        file_path=f"{role}.cs",
        symbol_name=path,
        confidence=0.85 if role == "provider" else 0.75,
        meta={"method": method, "path": path},
    )


@pytest.mark.parametrize(
    ("pattern", "forwarded", "expected"),
    [
        ("/catalog-api/{everything}", "/{everything}", ""),
        ("/posts/{id}", "/api/posts/{id}", "/api/posts"),
        ("/api/{**catch-all}", "/v2/{**catch-all}", "/v2"),
        ("/api/{*rest}", "/v2/{**rest}", "/v2"),  # one variable, two spellings
        ("/a/{x}/{y}", "/b/{y}/{x}", None),  # reordered
        ("/api/{version}/c/{everything}", "/api/{version}/{everything}", None),  # literal after a variable
        ("/product-images/{id}", "/api/catalog/items/{id}/pic", None),
        ("/values", "/api/values", None),  # one path, not a prefix
        ("/files/{name}.json", "/{name}.json", None),
    ],
)
def test_template_upstream(pattern: str, forwarded: str, expected: str | None) -> None:
    assert template_upstream(pattern, forwarded) == expected


def test_eshoponcontainers_ocelot_gateway_routes_each_service() -> None:
    root = FIXTURES / "eshoponc"
    m = build_service_name_map({a: root / a for a in ESHOPONC}, root)
    assert _routes(m) == {
        # /api/{version}/<letter>/{everything}: three routes under one literal prefix,
        # none a prefix rewrite. Blockers dispute nothing, so each keeps its edge.
        ("web.bff.shopping", "/api", None, "catalog.api"),
        ("web.bff.shopping", "/api", None, "basket.api"),
        ("web.bff.shopping", "/api", None, "ordering.api"),
        ("web.bff.shopping", "/orders-api", "", "ordering.api"),
        ("web.bff.shopping", "/hub", "", "ordering.signalrhub"),
        ("web.bff.shopping", "/basket-api", "", "basket.api"),
        ("web.bff.shopping", "/catalog-api", "", "catalog.api"),
        ("web.bff.shopping", "/payment-api", "", "payment.api"),
    }
    contracts = [
        _http("catalog.api", "provider", "GET", "/api/v1/catalog/items"),
        _http("web.bff.shopping", "consumer", "GET", "/catalog-api/api/v1/catalog/items"),
        _http("web.bff.shopping", "consumer", "GET", "/api/v1/c/catalog/items"),  # not rewritten
    ]
    annotate_consumer_targets(contracts, {}, m)
    assert contracts[1].meta["route_path"] == "/api/v1/catalog/items"
    assert "route_path" not in contracts[2].meta
    state = run_matching(contracts, m.routes)
    assert [(lk.consumer_repo, lk.provider_repo, lk.strategy) for lk in state.links] == [
        ("web.bff.shopping", "catalog.api", LinkStrategy.GATEWAY_ROUTE)
    ]
    assert {lk.provider_repo for lk in state.service_links} == set(ESHOPONC[1:])


def test_ocelot_route_shapes() -> None:
    text = """{
      // Ocelot 16 renamed ReRoutes; both are read.
      "Routes": [
        {"UpstreamPathTemplate": "/api/orders/{everything}", "DownstreamPathTemplate": "/{everything}",
         "UpstreamHttpMethod": ["Get"], "DownstreamHostAndPorts": [{"Host": "Ordering", "Port": 80}]},
        {"UpstreamPathTemplate": "/api/c/{all}", "DownstreamPathTemplate": "/api/{all}",
         "ServiceName": "catalog", "DownstreamHostAndPorts": [{"Host": "localhost", "Port": 1}]},
        {"UpstreamPathTemplate": "/api/pool/{all}", "DownstreamPathTemplate": "/{all}",
         "DownstreamHostAndPorts": [{"Host": "a", "Port": 80}, {"Host": "b", "Port": 80}]},
        {"UpstreamPathTemplate": "/api/host/{all}", "DownstreamPathTemplate": "/{all}",
         "UpstreamHost": "shop.example.com", "ServiceName": "basket"},
        {"UpstreamPathTemplate": "/api/verb/{all}", "DownstreamPathTemplate": "/{all}",
         "DownstreamHttpMethod": "POST", "ServiceName": "basket"},
        {"UpstreamPathTemplate": "/{everything}", "DownstreamPathTemplate": "/{everything}",
         "ServiceName": "web"},
      ],
    }"""
    assert _read(ocelot, text, "ocelot.json") == [
        ("ordering", Route("/api/orders", "")),
        ("catalog", Route("/api/c", "/api")),  # service discovery wins over hosts
        ("", Route("/api/pool", "")),  # two hosts: no one service
        ("basket", Route("/api/host", None)),
        ("basket", Route("/api/verb", None)),
    ]
    assert _read(ocelot, '{"ReRoutes": [{"UpstreamPathTemplate": "/x/{a}", '
                 '"DownstreamPathTemplate": "/{a}", "ServiceName": "x"}]}', "configuration.json") == [
        ("x", Route("/x", ""))
    ]
    assert _read(ocelot, "{ not json", "ocelot.json") == []


def test_yarp_route_shapes() -> None:
    text = """{
      "Logging": {},
      "ReverseProxy": {
        "Routes": {
          "catalog": {"ClusterId": "catalog", "Match": {"Path": "/catalog/{**catch-all}"},
                      "Transforms": [{"PathRemovePrefix": "/catalog"}, {"RequestHeader": "X", "Set": "1"}]},
          "basket": {"ClusterId": "basket", "Match": {"Path": "/basket/{**rest}", "Methods": ["GET"]},
                     "Transforms": [{"PathPattern": "/api/v1/basket/{**rest}"}]},
          "prefixed": {"ClusterId": "Basket", "Match": {"Path": "/b2/{**rest}"},
                       "Transforms": [{"PathPrefix": "/v2"}]},
          "deep": {"ClusterId": "catalog", "Match": {"Path": "/c2/{**rest}"},
                   "Transforms": [{"PathRemovePrefix": "/c2/items"}]},
          "set": {"ClusterId": "catalog", "Match": {"Path": "/set/{**rest}"},
                  "Transforms": [{"PathSet": "/x"}]},
          "hosted": {"ClusterId": "catalog", "Match": {"Path": "/h/{**rest}", "Hosts": ["a.example.com"]}},
          "exact": {"ClusterId": "catalog", "Match": {"Path": "/health"}},
          "pool": {"ClusterId": "pool", "Match": {"Path": "/pool/{**rest}"}},
          "one": {"ClusterId": "catalog", "Match": {"Path": "/posts/{id}"},
                  "Transforms": [{"PathPattern": "/api/posts/{id}"}]},
          "bad": {"ClusterId": "bad", "Match": {"Path": "/bad/{**rest}"}},
          "all": {"ClusterId": "catalog", "Match": {"Path": "{**catch-all}"}},
        },
        "Clusters": {
          "catalog": {"Destinations": {"d1": {"Address": "https+http://catalog-api"}}},
          "basket": {"Destinations": {"d1": {"Address": "http://basket-api:8080/base/"}}},
          "pool": {"Destinations": {"a": {"Address": "http://a/"}, "b": {"Address": "http://b/"}}},
          "bad": {"Destinations": {"d1": {"Address": "http://[bad"}}},
        },
      },
    }"""
    assert _read(yarp, text, "appsettings.json") == [
        ("catalog-api", Route("/catalog", "")),
        ("basket-api", Route("/basket", "/base/api/v1/basket")),
        ("basket-api", Route("/b2", "/base/v2/b2")),  # cluster ids bind case-insensitively
        ("catalog-api", Route("/c2", None)),  # removes part of the wildcard
        ("catalog-api", Route("/set", None)),
        ("catalog-api", Route("/h", None)),
        ("catalog-api", Route("/health", None)),
        ("", Route("/pool", "/pool")),  # two hosts: no one service
        ("catalog-api", Route("/posts", None)),  # {id} matches one segment, not a prefix
        ("", Route("/bad", None)),  # an address that does not parse
    ]


def test_eshop_map_forwarder_is_a_gateway_edge_without_a_rewrite() -> None:
    root = FIXTURES / "eshop"
    m = build_service_name_map({a: root / a for a in ("webapp", "catalog.api", "eshop.apphost")}, root)
    # /product-images/{id} goes out as /api/catalog/items/{id}/pic: not a prefix rewrite.
    assert _routes(m) == {("webapp", "/product-images", None, "catalog.api")}
    [route] = m.routes
    assert (route.name, route.source, route.file) == ("catalog-api", "yarp_forwarder", "webapp/Program.cs")
    state = run_matching([], m.routes)
    assert [(lk.consumer_repo, lk.provider_repo, lk.strategy) for lk in state.service_links] == [
        ("webapp", "catalog.api", LinkStrategy.GATEWAY_ROUTE)
    ]


def test_map_forwarder_shapes() -> None:
    text = """
    app.MapForwarder("/api/{**rest}", "http://catalog:8080");
    app.MapForwarder("/v1/{**rest}", "http://catalog/base", "/v2/{**rest}");
    app.MapForwarder("/cfg/{**rest}", "http://catalog", requestConfig);
    // app.MapForwarder("/old/{**rest}", "http://catalog");
    app.MapForwarder("/{**rest}", "http://catalog");
    app.MapForwarder("/one/{id}", "http://catalog");
    var u = "http://x"; app.MapForwarder("/same-line/{**rest}", "http://catalog");
    """
    assert _read(yarp_forwarder, text, "Program.cs") == [
        ("catalog", Route("/api", "/api")),  # forwarded unchanged
        ("catalog", Route("/v1", "/base/v2")),
        ("catalog", Route("/cfg", None)),
        ("catalog", Route("/one", None)),
        ("catalog", Route("/same-line", "/same-line")),
    ]
