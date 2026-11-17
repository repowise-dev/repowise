"""Server base paths (Spring context-path): read from config, placed by the
service-name map, applied to the routes they prefix before matching."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.workspace.contracts import Contract
from repowise.core.workspace.matching import annotate_consumer_targets, run_matching
from repowise.core.workspace.matching.base_paths import apply_base_paths
from repowise.core.workspace.origins import LinkStrategy
from repowise.core.workspace.service_names import (
    BasePath,
    ServiceName,
    ServiceNameMap,
    build_service_name_map,
)
from repowise.core.workspace.service_names.base import HOST, SourceFile
from repowise.core.workspace.service_names.spring_base_path import read

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "service_names"


def _paths(m: ServiceNameMap) -> set[tuple[str, str, str]]:
    return {(b.repo, b.module, b.path) for b in m.base_paths}


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_a_config_servers_app_file_sets_that_services_base_path() -> None:
    # piggymetrics: the context-path lives in the config repo, not the service.
    root = FIXTURES / "spring_pm"
    m = build_service_name_map({a: root / a for a in ("account-service", "auth-service", "config")})
    assert _paths(m) == {("account-service", "", "/accounts"), ("auth-service", "", "/uaa")}
    [auth] = [b for b in m.base_paths if b.repo == "auth-service"]
    assert (auth.source, auth.file) == (
        "spring_base_path",
        "config/src/main/resources/shared/auth-service.yml",
    )


@pytest.mark.parametrize(
    ("name", "text", "path"),
    [
        ("application.yml", "server:\n  servlet:\n    context-path: /api/\n", "/api"),
        ("application.properties", "server.servlet.contextPath=/api\n", "/api"),
        ("application.yml", "server:\n  context_path: /legacy\n", "/legacy"),
        ("bootstrap.yml", "spring:\n  webflux:\n    base-path: /flux\n", "/flux"),
        ("application.yml", "server.servlet.context-path: /dotted\n", "/dotted"),
        # Naming the active profile does not scope the document.
        ("application.yml", "spring.profiles.active: dev\nserver.context-path: /act\n", "/act"),
    ],
)
def test_every_spelling_is_read(name: str, text: str, path: str) -> None:
    [claim] = read(SourceFile(Path("svc/src/main/resources") / name, text))
    assert (claim.base_path, claim.at) == (path, Path("svc"))


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        # A placeholder, a root path, and a non-Spring key say nothing.
        ("svc/src/main/resources/application.yml", "server:\n  servlet:\n    context-path: ${CTX}\n"),
        ("svc/src/main/resources/application.yml", "server:\n  servlet:\n    context-path: /\n"),
        ("svc/src/main/resources/openapi.yml", "basePath: /v2\n"),
        # Two profiles disagreeing: no one value to trust.
        (
            "svc/src/main/resources/application.yml",
            "server.servlet.context-path: /a\n---\nserver.servlet.context-path: /b\n",
        ),
        # A config server's shared defaults would apply to every app at once.
        ("config/src/main/resources/shared/application.yml", "server.servlet.context-path: /x\n"),
        ("cfg/config/application.yml", "server.servlet.context-path: /x\n"),
        # A profile file holds only while that profile is active.
        ("svc/src/main/resources/application-dev.yml", "server.servlet.context-path: /x\n"),
        # So does a profile-scoped document in the default file, in any spelling.
        (
            "svc/src/main/resources/application.yml",
            "server.port: 80\n---\nspring.config.activate.on-profile: docker\n"
            "server.servlet.context-path: /x\n",
        ),
        (
            "svc/src/main/resources/application.yml",
            "server.port: 80\n---\nspring:\n  profiles: docker\n"
            "server:\n  servlet:\n    context-path: /x\n",
        ),
        (
            "svc/src/main/resources/application.properties",
            "server.port=80\n#---\nspring.config.activate.on-profile=docker\n"
            "server.servlet.context-path=/x\n",
        ),
    ],
)
def test_what_is_not_a_base_path(rel: str, text: str) -> None:
    assert list(read(SourceFile(Path(rel), text))) == []


def test_a_config_server_file_names_the_app_it_is_called() -> None:
    [claim] = read(SourceFile(Path("shared/orders.yml"), "server.servlet.context-path: /orders\n"))
    assert (claim.name, claim.base_path, claim.at) == ("orders", "/orders", None)


def test_a_name_only_a_deploy_file_places_says_no_module(tmp_path: Path) -> None:
    # compose places auth-service in `backend`, but not which of its modules.
    _write(tmp_path, "infra/docker-compose.yml",
           "services:\n  auth-service:\n    build: ../backend/auth\n")
    _write(tmp_path, "backend/auth/pom.xml", "<project/>\n")
    _write(tmp_path, "backend/orders/pom.xml", "<project/>\n")
    _write(tmp_path, "cfg/shared/auth-service.yml", "server.servlet.context-path: /uaa\n")
    m = build_service_name_map({a: tmp_path / a for a in ("infra", "backend", "cfg")})
    assert m.lookup("auth-service") is not None
    assert m.base_paths == ()


def test_each_module_of_a_monorepo_keeps_its_own_base_path(tmp_path: Path) -> None:
    _write(tmp_path, "mono/orders/src/main/resources/application.yml",
           "spring.application.name: orders\nserver.servlet.context-path: /orders\n")
    _write(tmp_path, "mono/billing/src/main/resources/application.yml",
           "spring.application.name: billing\n")
    _write(tmp_path, "cfg/config/billing.yml", "server.servlet.context-path: /bill\n")
    # Two values for one module: neither is applied.
    _write(tmp_path, "mono/stock/src/main/resources/application.yml",
           "spring.application.name: stock\nserver.servlet.context-path: /stock\n")
    _write(tmp_path, "cfg/config/stock.yml", "server.servlet.context-path: /inventory\n")
    m = build_service_name_map({"mono": tmp_path / "mono", "cfg": tmp_path / "cfg"})
    assert _paths(m) == {("mono", "orders", "/orders"), ("mono", "billing", "/bill")}


def test_base_paths_survive_the_stored_map() -> None:
    m = ServiceNameMap(base_paths=(BasePath("auth", "", "/uaa", "spring_base_path", "cfg/a.yml"),))
    assert ServiceNameMap.from_dict(m.to_dict("fp")).base_paths == m.base_paths


# ---------------------------------------------------------------------------
# Applying and matching
# ---------------------------------------------------------------------------


def _http(repo: str, role: str, path: str, method: str = "GET", file: str = "A.java", **meta: object) -> Contract:
    return Contract(
        repo=repo,
        contract_id=f"http::{method}::{path}",
        contract_type="http",
        role=role,
        file_path=file,
        symbol_name=path,
        confidence=0.9,
        meta={"method": method, "path": path, **meta},
    )


def _spring(repo: str, path: str, method: str = "GET", file: str = "A.java") -> Contract:
    return _http(repo, "provider", path, method, file, framework="spring")


def test_a_base_path_prefixes_spring_routes_and_is_recomputed_not_stacked() -> None:
    account = [_spring("account", "/{param}"), _spring("account", "/", "POST")]
    express = _http("account", "provider", "/health", framework="express")
    contracts = [*account, express]
    base = (BasePath("account", "", "/accounts", "spring_base_path", "config/x.yml"),)
    apply_base_paths(contracts, base)
    apply_base_paths(contracts, base)
    assert [c.contract_id for c in contracts] == [
        "http::GET::/accounts/{param}",
        "http::POST::/accounts",
        "http::GET::/health",  # not Spring: the context-path is Spring's
    ]
    apply_base_paths(contracts, ())  # the config line went away
    assert [c.contract_id for c in account] == ["http::GET::/{param}", "http::POST::/"]
    assert all("declared_path" not in c.meta for c in account)


def test_a_module_base_path_stays_in_its_module() -> None:
    orders = _spring("mono", "/{param}", file="orders/src/O.java")
    billing = _spring("mono", "/{param}", file="billing/src/B.java")
    apply_base_paths([orders, billing], (BasePath("mono", "orders", "/orders", "s", "f"),))
    assert (orders.contract_id, billing.contract_id) == (
        "http::GET::/orders/{param}",
        "http::GET::/{param}",
    )


def test_a_call_naming_the_context_path_links_to_the_route() -> None:
    # piggymetrics: AuthServiceClient posts /uaa/users; UserController maps /users.
    contracts = [
        _spring("auth", "/users", "POST"),
        _spring("account", "/{param}"),
        _spring("statistics", "/{param}"),
        _http("account", "consumer", "/uaa/users", "POST", host="auth-service"),
        _http("notification", "consumer", "/accounts/{param}", host="account-service"),
    ]
    names = ServiceNameMap(
        entries={
            (HOST, n): ServiceName(n, HOST, r, "spring_application_name", f"{r}/bootstrap.yml")
            for n, r in (("auth-service", "auth"), ("account-service", "account"))
        },
        repos=frozenset({"auth", "account", "statistics", "notification"}),
        base_paths=(
            BasePath("auth", "", "/uaa", "spring_base_path", "config/auth-service.yml"),
            BasePath("account", "", "/accounts", "spring_base_path", "config/account-service.yml"),
            BasePath("statistics", "", "/statistics", "spring_base_path", "config/s.yml"),
        ),
    )
    apply_base_paths(contracts, names.base_paths)
    annotate_consumer_targets(contracts, {}, names)
    state = run_matching(contracts)
    assert sorted((lk.consumer_repo, lk.provider_repo, lk.contract_id, lk.strategy) for lk in state.links) == [
        ("account", "auth", "http::POST::/uaa/users", LinkStrategy.SERVICE_MAPPED),
        ("notification", "account", "http::GET::/accounts/{param}", LinkStrategy.SERVICE_MAPPED),
    ]
    assert state.service_links == []


def test_a_star_segment_matches_a_parameter() -> None:
    # petclinic: @RequestMapping("/owners/*/pets") on PetResource.
    from repowise.core.workspace.extractors.http.paths import normalize_http_path

    assert normalize_http_path("/owners/*/pets/{petId}") == "/owners/{param}/pets/{param}"
    assert normalize_http_path("/**") == "/**"  # a catch-all is not one segment
    provider = _spring("customers", normalize_http_path("/owners/*/pets/{petId}"), "PUT")
    consumer = _http("web", "consumer", "/owners/{param}/pets/{param}", "PUT")
    [link] = run_matching([provider, consumer]).links
    assert link.provider_repo == "customers"
