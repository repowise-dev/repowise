"""Gateway routes: read from config, settled in the service-name map, applied by the matcher."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.workspace.contracts import Contract, ContractStore
from repowise.core.workspace.cross_repo import CrossRepoOverlay
from repowise.core.workspace.matching import annotate_consumer_targets, run_matching
from repowise.core.workspace.origins import LinkStrategy
from repowise.core.workspace.service_names import (
    GatewayRoute,
    ServiceName,
    ServiceNameMap,
    build_service_name_map,
)
from repowise.core.workspace.service_names.base import (
    EXACT,
    PARTIAL,
    PREFIX,
    Route,
    SourceFile,
    literal_prefix,
)
from repowise.core.workspace.service_names.spring_gateway import read
from repowise.core.workspace.system_graph import build_system_graph

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "service_names"


def _routes(m: ServiceNameMap) -> set[tuple[str, str, str | None, str]]:
    return {(r.gateway, r.prefix, r.upstream, r.repo) for r in m.routes}


def _read(name: str, text: str) -> list[tuple[str, Route]]:
    return [(c.name, c.route) for c in read(SourceFile(Path(name), text)) if c.route is not None]


# ---------------------------------------------------------------------------
# Sources, on real files
# ---------------------------------------------------------------------------


def test_spring_cloud_gateway_routes_strip_their_prefix() -> None:
    root = FIXTURES / "spring_pc"
    gateway, customers, vets = (
        "spring-petclinic-api-gateway",
        "spring-petclinic-customers-service",
        "spring-petclinic-vets-service",
    )
    m = build_service_name_map({a: root / a for a in (gateway, customers, vets)})
    # visits-service and genai-service are routed outside this workspace: blockers only.
    assert _routes(m) == {
        (gateway, "/api/customer", "", customers),
        (gateway, "/api/vet", "", vets),
        (gateway, "/api/visit", "", ""),
        (gateway, "/api/genai", "", ""),
    }
    [route] = [r for r in m.routes if r.repo == vets]
    assert (route.name, route.source) == ("vets-service", "spring_gateway")
    assert route.file == f"{gateway}/src/main/resources/application.yml"


def test_zuul_routes_in_a_config_server_file_belong_to_the_app_it_names() -> None:
    root = FIXTURES / "spring_pm"
    repos = ("account-service", "auth-service", "config", "gateway")
    m = build_service_name_map({a: root / a for a in repos})
    # stripPrefix: false keeps the prefix; the file sits in config, the gateway is `gateway`.
    assert _routes(m) == {
        ("gateway", "/accounts", "/accounts", "account-service"),
        ("gateway", "/uaa", "/uaa", "auth-service"),
        ("gateway", "/notifications", "/notifications", ""),
        ("gateway", "/statistics", "/statistics", ""),
    }
    # Without the gateway's own module the owner is unknown: no route, not the config repo.
    assert build_service_name_map({a: root / a for a in repos[:3]}).routes == ()


# ---------------------------------------------------------------------------
# The reader's shapes
# ---------------------------------------------------------------------------


def test_properties_long_form_and_path_filters() -> None:
    text = "\n".join(
        [
            "spring.cloud.gateway.routes[0].uri=lb://orders",
            "spring.cloud.gateway.routes[0].predicates[0]=Path=/api/orders/**,/v2/orders/**",
            "spring.cloud.gateway.routes[0].filters[0]=StripPrefix=1",
            "spring.cloud.gateway.routes[0].filters[1]=PrefixPath=/internal",
            "spring.cloud.gateway.routes[1].uri=http://billing:8080",
            "spring.cloud.gateway.routes[1].predicates[0]=Path=/bill/**",
            r"spring.cloud.gateway.routes[1].filters[0]=RewritePath=/bill/?(?<seg>.*), /v1/$\{seg}",
        ]
    )
    assert _read("src/main/resources/application.properties", text) == [
        ("orders", Route("/api/orders", "/internal/orders")),
        ("orders", Route("/v2/orders", "/internal/orders")),
        ("billing", Route("/bill", "/v1")),
    ]


def test_yaml_long_form_and_routes_it_can_only_block() -> None:
    text = """
spring:
  cloud:
    gateway:
      mvc:
        routes:
          - id: users
            uri: lb:ws://users
            predicates:
              - name: Path
                args:
                  patterns: /users/**
            filters:
              - name: StripPrefix
                args:
                  parts: 1
          - {id: exact, uri: lb://a, predicates: [Path=/health]}
          - {id: all, uri: lb://b, predicates: [Path=/**]}
          - {id: deep, uri: lb://c, predicates: ["Path=/x/{y}/**"], filters: [StripPrefix=2]}
          - {id: setpath, uri: lb://d, predicates: [Path=/d/**], filters: [SetPath=/z]}
          - {id: partial, uri: lb://e, predicates: ["Path=/api/orders*"]}
          - {id: method, uri: lb://f, predicates: [Path=/f/**, Method=GET]}
          - {id: local, uri: forward:/local, predicates: [Path=/local/**]}
"""
    g = ("gateway-dev", "gateway")
    assert _read("gateway-dev.yml", text) == [
        ("users", Route("/users", "", g)),
        ("a", Route("/health", None, g)),
        ("c", Route("/x", None, g)),
        ("d", Route("/d", None, g)),
        ("e", Route("/api", None, g)),
        ("f", Route("/f", None, g)),
        ("", Route("/local", "/local", g)),  # no target: settles as a blocker
    ]


def test_zuul_defaults_and_global_prefix() -> None:
    text = """
zuul:
  prefix: /api
  strip_prefix: true
  routes:
    users: /myusers/**
    orders:
      path: /orders/**
      url: http://orders-svc:8080
      stripPrefix: false
    local:
      path: /local/**
      url: forward:/here
"""
    assert _read("src/main/resources/application.yml", text) == [
        ("users", Route("/api/myusers", "")),
        ("orders-svc", Route("/api/orders", "/orders")),
        ("", Route("/api/local", "")),
    ]


def test_which_files_name_a_gateway() -> None:
    text = "zuul:\n  routes:\n    users: /users/**\n"
    # A profile suffix is dropped; any other last word is part of the name.
    assert [r.gateway for _, r in _read("api-gateway-docker.yml", text)] == [
        ("api-gateway-docker", "api-gateway")
    ]
    assert [r.gateway for _, r in _read("api-gateway.yml", text)] == [("api-gateway",)]
    # An application file outside a module's own resources configures every app.
    assert _read("config/src/main/resources/shared/application.yml", text) == []
    assert [r.gateway for _, r in _read("gw/src/main/resources/config/application.yml", text)] == [()]


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("/api/vet/**", ("/api/vet", PREFIX)),
        ("/Orders/{id}", ("/orders", PREFIX)),
        ("/health", ("/health", EXACT)),
        ("/api/orders*", ("/api", PARTIAL)),
        ("/api/v{n}/x/**", ("/api", PARTIAL)),
        ("/**", ("", PREFIX)),
    ],
)
def test_literal_prefix(pattern: str, expected: tuple[str, str]) -> None:
    assert literal_prefix(pattern) == expected


def test_a_disputed_or_unknown_route_blocks_shorter_prefixes(tmp_path: Path) -> None:
    for name, svc in (("gw", None), ("a", "svc-a"), ("b", "svc-b")):
        res = tmp_path / name / "src" / "main" / "resources"
        res.mkdir(parents=True)
        if svc:
            (res / "application.yml").write_text(f"spring:\n  application:\n    name: {svc}\n")
    gw_res = tmp_path / "gw" / "src" / "main" / "resources"
    rows = [
        ("lb://svc-a", "/api/**"),
        ("lb://svc-a", "/api/vet/**"),
        ("lb://svc-b", "/api/vet/**"),  # the same prefix, another service
        ("lb://elsewhere", "/api/pay/**"),  # a service outside the workspace
        ("lb://svc-b", "/api/visit/**"),
    ]
    (gw_res / "application.properties").write_text(
        "".join(
            f"spring.cloud.gateway.routes[{i}].uri={uri}\n"
            f"spring.cloud.gateway.routes[{i}].predicates[0]=Path={path}\n"
            for i, (uri, path) in enumerate(rows)
        )
    )
    m = build_service_name_map({n: tmp_path / n for n in ("gw", "a", "b")})
    assert _routes(m) == {
        ("gw", "/api", "/api", "a"),
        ("gw", "/api/vet", None, ""),
        ("gw", "/api/pay", "/api/pay", ""),
        ("gw", "/api/visit", "/api/visit", "b"),
    }
    assert m.route_for("gw", "/api/vet/x") is None
    assert m.route_for("gw", "/api/pay/x") is None
    assert m.route_for("gw", "/api/visit/x")[0].repo == "b"  # type: ignore[index]
    assert m.route_for("gw", "/api/other")[0].repo == "a"  # type: ignore[index]
    # Blockers draw no edge.
    edges = {lk.provider_repo for lk in run_matching([], m.routes).service_links}
    assert edges == {"a", "b"}


# ---------------------------------------------------------------------------
# What the matcher does with them
# ---------------------------------------------------------------------------


def _http(repo: str, role: str, path: str, method: str = "GET", file: str = "", **meta) -> Contract:
    return Contract(
        repo=repo,
        contract_id=f"http::{method}::{path}",
        contract_type="http",
        role=role,
        file_path=file or f"{role}.js",
        symbol_name=path,
        confidence=0.85 if role == "provider" else 0.75,
        meta={"method": method, "path": path, **meta},
    )


def _gateway_map() -> ServiceNameMap:
    m = ServiceNameMap(repos=frozenset({"gw", "customers", "vets", "web"}))
    m.entries[("host", "gateway")] = ServiceName("gateway", "host", "gw", "spring_application_name", "gw/app.yml")
    m.routes = (
        GatewayRoute("gw", "/api/customer", "", "customers", "customers-service", "spring_gateway", "gw/app.yml"),
        GatewayRoute("gw", "/api/vet", "", "vets", "vets-service", "spring_gateway", "cfg/gw.yml"),
    )
    return m


def test_calls_through_a_gateway_link_on_the_forwarded_path() -> None:
    contracts = [
        _http("customers", "provider", "/owners/{param}"),
        _http("vets", "provider", "/vets"),
        _http("vets", "provider", "/owners/{param}"),  # same path, wrong service
        # The gateway's own page, relative; written twice in one file.
        _http("gw", "consumer", "/api/customer/owners/{param}", file="Owner.js"),
        _http("gw", "consumer", "/api/customer/owners/{param}", file="Owner.js"),
        # Another repo calling the gateway by its host.
        _http("web", "consumer", "/api/vet/vets", host="gateway"),
        # Routed, but nothing there serves the path: a service-level edge.
        _http("gw", "consumer", "/api/vet/specialties", file="Vets.js"),
        # A relative call from another repo is not routed.
        _http("web", "consumer", "/api/customer/pets", file="Pets.js"),
    ]
    m = _gateway_map()
    annotate_consumer_targets(contracts, {}, m)
    assert contracts[3].meta["route_path"] == "/owners/{param}"
    assert contracts[3].meta["target_repo"] == "customers"
    assert contracts[3].meta["target_via"] == "spring_gateway"
    assert "route_path" not in contracts[-1].meta

    state = run_matching(contracts, m.routes)
    assert [(lk.consumer_repo, lk.provider_repo, lk.contract_id, lk.strategy) for lk in state.links] == [
        ("gw", "customers", "http::GET::/owners/{param}", LinkStrategy.GATEWAY_ROUTE),
        ("web", "vets", "http::GET::/vets", LinkStrategy.GATEWAY_ROUTE),
    ]
    assert state.links[0].consumer_contract_id == "http::GET::/api/customer/owners/{param}"
    assert state.links[0].match_type == "exact"

    by_strategy = {(lk.strategy, lk.consumer_repo, lk.provider_repo, lk.consumer_file) for lk in state.service_links}
    assert by_strategy == {
        (LinkStrategy.HOST_ONLY, "gw", "vets", "Vets.js"),
        (LinkStrategy.GATEWAY_ROUTE, "gw", "customers", "app.yml"),
        # The route file sits in a config repo: the gateway's repo root.
        (LinkStrategy.GATEWAY_ROUTE, "gw", "vets", ""),
    }

    graph = build_system_graph(contracts, state.links, CrossRepoOverlay(), {}, service_links=state.service_links)
    edges = {(e.source, e.target, e.kind) for e in graph.edges}
    assert edges == {("gw", "customers", "http"), ("gw", "vets", "http"), ("web", "vets", "http")}


def test_routes_round_trip_and_stale_route_meta_is_cleared() -> None:
    m = _gateway_map()
    again = ServiceNameMap.from_dict(m.to_dict("fp"))
    assert again.routes == m.routes

    consumer = _http("gw", "consumer", "/api/vet/vets")
    annotate_consumer_targets([consumer], {}, m)
    assert consumer.meta["route_path"] == "/vets"
    annotate_consumer_targets([consumer], {}, ServiceNameMap())
    assert "route_path" not in consumer.meta and "target_repo" not in consumer.meta

    store = ContractStore(service_links=run_matching([], m.routes).service_links)
    assert len(ContractStore.from_dict(store.to_dict()).service_links) == 2
