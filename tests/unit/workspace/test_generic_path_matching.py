"""A generic operational path (``/health``, ``/metrics``) never links across repos by path alone."""

from __future__ import annotations

import pytest

from repowise.core.workspace.contracts import Contract
from repowise.core.workspace.matching import annotate_consumer_targets, match_contracts
from repowise.core.workspace.origins import ORIGIN_CONFIDENCE, ContractOrigin, LinkStrategy


def _http(repo: str, role: str, path: str, service: str | None = None, **meta) -> Contract:
    origin = ContractOrigin.ROUTE_REGISTRATION if role == "provider" else ContractOrigin.NAME_HEURISTIC
    return Contract(
        repo=repo,
        contract_id=f"http::GET::{path}",
        contract_type="http",
        role=role,
        file_path=f"{repo}/{service or 'app'}.ts",
        symbol_name=path,
        confidence=ORIGIN_CONFIDENCE[origin],
        meta={"method": "GET", "path": path, **meta},
        origin=origin,
        service=service,
    )


def _links(contracts: list[Contract], service_bases: dict[str, str] | None = None):
    annotate_consumer_targets(contracts, service_bases)
    return [(link.provider_repo, link.strategy) for link in match_contracts(contracts)]


@pytest.mark.parametrize(
    ("consumer_path", "provider_path", "meta"),
    [
        ("/health", "/health", {"base_stripped": True, "base_token": "url"}),  # unique_path
        ("/healthz", "/healthz", {}),  # exact pass, relative call
        ("/api/v1/metrics", "/metrics", {}),  # candidate pass
        ("/", "/", {"base_stripped": True}),
        ("/actuator/health", "/actuator/health", {"host": "localhost"}),
        ("/{param}/status", "/{param}/status", {}),
        ("/{param}/health", "/health", {}),
    ],
)
def test_generic_path_does_not_link_to_another_repo(consumer_path, provider_path, meta) -> None:
    contracts = [_http("backend", "provider", provider_path), _http("web", "consumer", consumer_path, **meta)]
    assert _links(contracts) == []


def test_generic_path_links_where_the_base_names_the_repo() -> None:
    contracts = [
        _http("backend", "provider", "/health"),
        _http("web", "consumer", "/health", base_stripped=True, base_token="backend_url"),
    ]
    assert _links(contracts, {"BACKEND_URL": "backend"}) == [("backend", LinkStrategy.SERVICE_MAPPED)]


def test_generic_path_prefers_the_server_in_its_own_repo() -> None:
    contracts = [
        _http("backend", "provider", "/health"),
        _http("tool", "provider", "/health", service="server"),
        _http("tool", "consumer", "/health", service="cli", base_stripped=True),
    ]
    assert _links(contracts) == [("tool", LinkStrategy.UNIQUE_PATH)]


@pytest.mark.parametrize(
    "path", ["/health/coordinator", "/status/{param}", "/repos/{param}/health", "/{param}", "/api/{param}"]
)
def test_a_specific_path_still_links_by_path(path: str) -> None:
    contracts = [_http("backend", "provider", path), _http("web", "consumer", path, base_stripped=True)]
    assert _links(contracts) == [("backend", LinkStrategy.UNIQUE_PATH)]
