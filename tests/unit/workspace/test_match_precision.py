"""A call whose base points at another program, or that its own service serves, links to no other route."""

from __future__ import annotations

import pytest

from repowise.core.workspace.contracts import Contract
from repowise.core.workspace.diagnostics import UnmatchedReason, build_diagnostics
from repowise.core.workspace.matching import annotate_consumer_targets, match_contracts
from repowise.core.workspace.origins import ORIGIN_CONFIDENCE, ContractOrigin, LinkStrategy


def _http(
    role: str, path: str, service: str, *, method: str = "GET", repo: str = "mono", **meta
) -> Contract:
    origin = ContractOrigin.ROUTE_REGISTRATION if role == "provider" else ContractOrigin.NAME_HEURISTIC
    return Contract(
        repo=repo,
        contract_id=f"http::{method}::{path}",
        contract_type="http",
        role=role,
        file_path=f"{service}/{role}.ts",
        symbol_name=path,
        confidence=ORIGIN_CONFIDENCE[origin],
        meta={"method": method, "path": path, **meta},
        origin=origin,
        service=service,
    )


def _links(contracts: list[Contract], service_bases: dict[str, str] | None = None):
    annotate_consumer_targets(contracts, service_bases)
    return [(link.provider_file, link.strategy) for link in match_contracts(contracts)]


ELSEWHERE = {"base_stripped": True, "base_token": "RAG_API_URL"}


@pytest.mark.parametrize(
    ("consumer_path", "provider_path", "meta"),
    [
        ("/health", "/health", ELSEWHERE),  # unique_path, generic path
        ("/files", "/{param}/files", {"base_stripped": True, "base_token": "baseURL"}),  # prefix_stripped
        ("/bridge/pairings", "/{param}/pairings", {"base_stripped": True, "base_token": "trim"}),  # param_segment
        ("/files/{param}", "/{param}/files/{param}", {"host": "rag_api"}),  # unmapped host
        ("/health", "/health", {"host": "rag_api"}),  # exact pass, generic path
        ("/health", "/health", {"base_stripped": True, "base_token": "env.API_URL || window.location.origin"}),
    ],
)
def test_a_call_whose_base_points_elsewhere_takes_no_weak_link(consumer_path, provider_path, meta) -> None:
    contracts = [_http("provider", provider_path, "api"), _http("consumer", consumer_path, "packages/api", **meta)]
    assert _links(contracts) == []


@pytest.mark.parametrize(
    ("consumer_path", "provider_path", "meta", "strategy"),
    [
        ("/files", "/{param}/files", {}, LinkStrategy.PREFIX_STRIPPED),
        ("/files", "/{param}/files", {"host": "localhost"}, LinkStrategy.PREFIX_STRIPPED),
        ("/bridge/pairings", "/{param}/pairings", {}, LinkStrategy.PARAM_SEGMENT),
        ("/health", "/health", {"base_stripped": True}, LinkStrategy.UNIQUE_PATH),
        (
            "/files",
            "/{param}/files",
            {"base_stripped": True, "base_token": "window.location.origin"},
            LinkStrategy.PREFIX_STRIPPED,
        ),
        ("/files", "/{param}/files", {"base_stripped": True, "base_token": "origin"}, LinkStrategy.PREFIX_STRIPPED),
    ],
)
def test_a_call_with_no_base_or_a_local_host_keeps_its_weak_link(consumer_path, provider_path, meta, strategy) -> None:
    contracts = [_http("provider", provider_path, "api"), _http("consumer", consumer_path, "web", **meta)]
    assert _links(contracts) == [("api/provider.ts", strategy)]


def test_a_specific_path_behind_an_unresolved_base_still_links_by_path() -> None:
    contracts = [
        _http("provider", "/api/roadmap/stats", "backend"),
        _http("consumer", "/api/roadmap/stats", "frontend", base_stripped=True, base_token="API_BASE_URL"),
    ]
    assert _links(contracts) == [("backend/provider.ts", LinkStrategy.UNIQUE_PATH)]


def test_a_base_resolved_to_the_provider_repo_keeps_its_weak_links() -> None:
    contracts = [
        _http("provider", "/{param}/files", "api", repo="backend"),
        _http("consumer", "/files", "web", repo="web", base_stripped=True, base_token="backend_url"),
    ]
    assert _links(contracts, {"BACKEND_URL": "backend"}) == [("api/provider.ts", LinkStrategy.PREFIX_STRIPPED)]


def test_a_relative_call_its_own_service_serves_links_to_no_other() -> None:
    # A browser fetch to a Next.js route handler that forwards to the API.
    contracts = [
        _http("provider", "/api/v1/mindmaps", "frontend"),
        _http("provider", "/api/v1/mindmaps", "backend"),
        _http("consumer", "/api/v1/mindmaps", "frontend", client="fetch"),
    ]
    assert _links(contracts) == []
    diag = build_diagnostics(contracts, match_contracts(contracts))
    assert diag.unmatched_consumers == []
    assert [u.reason for u in diag.in_service_consumers] == [UnmatchedReason.INTERNAL_ONLY]


def test_a_wrapper_call_may_carry_a_backend_origin_so_it_keeps_its_link() -> None:
    contracts = [
        _http("provider", "/api/v1/mindmaps", "frontend"),
        _http("provider", "/api/v1/mindmaps", "backend"),
        _http("consumer", "/api/v1/mindmaps", "frontend", client="wrapper"),
    ]
    assert _links(contracts) == [("backend/provider.ts", LinkStrategy.EXACT_ID)]


def test_the_handler_forwarding_through_its_base_still_links_to_the_api() -> None:
    contracts = [
        _http("provider", "/api/v1/mindmaps", "frontend"),
        _http("provider", "/api/v1/mindmaps", "backend"),
        _http("consumer", "/api/v1/mindmaps", "frontend", base_stripped=True, base_token="BACKEND_URL"),
    ]
    assert _links(contracts) == [("backend/provider.ts", LinkStrategy.UNIQUE_PATH)]


def test_a_relative_call_its_service_does_not_serve_links_across_services() -> None:
    contracts = [_http("provider", "/api/v1/mindmaps", "backend"), _http("consumer", "/api/v1/mindmaps", "frontend")]
    assert _links(contracts) == [("backend/provider.ts", LinkStrategy.EXACT_ID)]
