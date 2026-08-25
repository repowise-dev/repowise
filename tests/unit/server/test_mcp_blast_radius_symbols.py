"""Symbol-id targets in get_blast_radius, and the enricher indexes behind them."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from repowise.core.workspace.contracts import Contract, ContractLink
from repowise.core.workspace.cross_repo import CrossRepoOverlay
from repowise.core.workspace.system_graph import build_system_graph
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._enrichment import CrossRepoEnricher
from repowise.server.mcp_server.tool_blast_radius import (
    _SYMBOL_CONSUMER_LIMIT,
    get_blast_radius,
)


def _enricher(tmp_path: Path, contracts, links, boundaries=None) -> CrossRepoEnricher:
    graph = build_system_graph(
        contracts, links, CrossRepoOverlay(), boundaries or {}, generated_at="t"
    )
    (tmp_path / "system_graph.json").write_text(json.dumps(graph.to_dict()), encoding="utf-8")
    (tmp_path / "contracts.json").write_text(
        json.dumps(
            {
                "contracts": [c.to_dict() for c in contracts],
                "contract_links": [lk.to_dict() for lk in links],
            }
        ),
        encoding="utf-8",
    )
    return CrossRepoEnricher(
        tmp_path / "cross_repo_edges.json",
        contracts_path=tmp_path / "contracts.json",
        system_graph_path=tmp_path / "system_graph.json",
    )


@contextlib.contextmanager
def _in_workspace(enricher: CrossRepoEnricher):
    prev_registry = _state._registry
    prev_enricher = _state._cross_repo_enricher
    _state._registry = object()
    _state._cross_repo_enricher = enricher
    try:
        yield
    finally:
        _state._registry = prev_registry
        _state._cross_repo_enricher = prev_enricher


def _provider(
    *,
    repo: str = "backend",
    contract_id: str = "code::@acme/types::Order",
    file_path: str = "src/types.ts",
    name: str = "Order",
    service: str | None = None,
    symbol_id: str | None = None,
) -> Contract:
    return Contract(
        repo=repo,
        contract_id=contract_id,
        contract_type="code",
        role="provider",
        file_path=file_path,
        symbol_name=name,
        confidence=0.9,
        service=service,
        symbol_id=symbol_id or f"{file_path}::{name}",
    )


def _consumer(
    *,
    repo: str = "frontend",
    contract_id: str = "code::@acme/types::Order",
    file_path: str = "src/api.ts",
    service: str | None = None,
) -> Contract:
    return Contract(
        repo=repo,
        contract_id=contract_id,
        contract_type="code",
        role="consumer",
        file_path=file_path,
        symbol_name="@acme/types:Order",
        confidence=0.9,
        service=service,
    )


def _link(provider: Contract, consumer: Contract) -> ContractLink:
    return ContractLink(
        contract_id=provider.contract_id,
        contract_type="code",
        match_type="exact",
        confidence=0.9,
        provider_repo=provider.repo,
        provider_file=provider.file_path,
        provider_symbol=provider.symbol_name,
        provider_service=provider.service,
        consumer_repo=consumer.repo,
        consumer_file=consumer.file_path,
        consumer_symbol=consumer.symbol_name,
        consumer_service=consumer.service,
        provider_symbol_id=provider.symbol_id,
    )


@pytest.mark.asyncio
async def test_a_consumer_symbol_is_not_a_publisher(tmp_path: Path):
    """A call site names a contract it consumes; it publishes no downstream."""
    provider = _provider()
    consumer = _consumer()
    consumer.symbol_id = "src/api.ts::useOrder"
    with _in_workspace(_enricher(tmp_path, [provider, consumer], [])):
        result = await get_blast_radius(["src/api.ts::useOrder"])
    assert result["unresolved_targets"] == ["src/api.ts::useOrder"]
    assert result["targets"] == []
    assert "symbol_targets" not in result


@pytest.mark.asyncio
async def test_an_ambiguous_symbol_id_says_so(tmp_path: Path):
    """Symbol ids are repo-relative, so one id can name two published symbols."""
    contracts = [
        _provider(
            repo=repo,
            contract_id=f"code::@acme/{repo}::main",
            file_path="src/index.ts",
            name="main",
        )
        for repo in ("api", "worker")
    ]
    with _in_workspace(_enricher(tmp_path, contracts, [])):
        result = await get_blast_radius(["src/index.ts::main"])
    block = result["symbol_targets"][0]
    assert block["ambiguous_in_repos"] == ["api", "worker"]
    assert block["nodes"] == ["api", "worker"]
    assert result["targets"] == ["api", "worker"]


@pytest.mark.asyncio
async def test_symbol_consumers_are_capped_and_counted(tmp_path: Path):
    over = _SYMBOL_CONSUMER_LIMIT + 4
    provider = _provider()
    consumers = [_consumer(file_path=f"src/api{i}.ts") for i in range(over)]
    links = [_link(provider, c) for c in consumers]
    with _in_workspace(_enricher(tmp_path, [provider, *consumers], links)):
        result = await get_blast_radius(["src/types.ts::Order"])
    block = result["symbol_targets"][0]
    assert len(block["consumers"]) == _SYMBOL_CONSUMER_LIMIT
    assert block["consumer_count"] == over
    assert block["consumers_truncated"] == 4
    # The summary reports the true count, not the capped list's length.
    assert f"{over} consumer(s) across the contract links" in result["summary"]


@pytest.mark.asyncio
async def test_a_service_scoped_symbol_resolves_to_its_service_node(tmp_path: Path):
    """Graph nodes come from service boundaries, not from ``Contract.service``."""
    from repowise.core.workspace.extractors.service_boundary import ServiceBoundary

    provider = _provider(file_path="packages/types/src/types.ts", service="packages/types")
    consumer = _consumer(file_path="app/src/api.ts", service="app")
    boundaries = {
        "backend": [ServiceBoundary(service_path="packages/types", service_name="types")],
        "frontend": [ServiceBoundary(service_path="app", service_name="app")],
    }
    enricher = _enricher(tmp_path, [provider, consumer], [_link(provider, consumer)], boundaries)
    with _in_workspace(enricher):
        result = await get_blast_radius(["packages/types/src/types.ts::Order"])
    assert result["symbol_targets"][0]["nodes"] == ["backend::packages/types"]
    assert result["targets"] == ["backend::packages/types"]
    assert {n["id"] for n in result["impacted"]} == {"frontend::app"}


@pytest.mark.asyncio
async def test_a_stale_contract_service_does_not_invent_a_node(tmp_path: Path):
    """``Contract.service`` naming no node falls back to the repo node."""
    provider = _provider(file_path="src/types.ts", service="packages/gone")
    with _in_workspace(_enricher(tmp_path, [provider], [])):
        result = await get_blast_radius(["src/types.ts::Order"])
    assert result["symbol_targets"][0]["nodes"] == ["backend"]
    assert result["unresolved_targets"] == []


@pytest.mark.asyncio
async def test_a_symbol_target_alongside_a_node_target_keeps_both(tmp_path: Path):
    provider = _provider()
    consumer = _consumer()
    with _in_workspace(_enricher(tmp_path, [provider, consumer], [_link(provider, consumer)])):
        result = await get_blast_radius(["frontend", "src/types.ts::Order"])
    assert result["targets"] == ["backend", "frontend"]
    assert [b["symbol_id"] for b in result["symbol_targets"]] == ["src/types.ts::Order"]


@pytest.mark.asyncio
async def test_a_repeated_symbol_target_is_reported_once(tmp_path: Path):
    provider = _provider()
    consumer = _consumer()
    with _in_workspace(_enricher(tmp_path, [provider, consumer], [_link(provider, consumer)])):
        result = await get_blast_radius(["src/types.ts::Order", "src/types.ts::Order"])
    assert len(result["symbol_targets"]) == 1
    assert "1 symbol target(s) have 1 consumer(s)" in result["summary"]


@pytest.mark.asyncio
async def test_a_nested_symbol_id_resolves(tmp_path: Path):
    """The three-segment form the parser mints for a method on a class."""
    provider = _provider(
        contract_id="http::GET::/users",
        file_path="UsersController.cs",
        name="aspnet:GET /users",
        symbol_id="UsersController.cs::UsersController::GetUsers",
    )
    with _in_workspace(_enricher(tmp_path, [provider], [])):
        result = await get_blast_radius(["UsersController.cs::UsersController::GetUsers"])
    assert result["targets"] == ["backend"]
    assert result["symbol_targets"][0]["nodes"] == ["backend"]


def test_enricher_symbol_indexes_survive_a_reload(tmp_path: Path):
    """reload() runs in the job executor; a missed reset would double every row."""
    provider = _provider()
    consumer = _consumer()
    enricher = _enricher(tmp_path, [provider, consumer], [_link(provider, consumer)])
    assert len(enricher.get_contracts_for_symbol("src/types.ts::Order")) == 1
    enricher.reload()
    assert len(enricher.get_contracts_for_symbol("src/types.ts::Order")) == 1
    assert len(enricher.get_contract_links_by_provider_symbol("src/types.ts::Order")) == 1
    assert enricher.get_contracts_for_symbol("nope.ts::Nope") == []
