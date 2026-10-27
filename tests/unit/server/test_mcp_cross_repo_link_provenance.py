"""How a cross-repo link was matched, and how far to trust the estate, in the MCP tools."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from repowise.core.workspace.contracts import Contract, ContractLink
from repowise.core.workspace.cross_repo import CrossRepoOverlay
from repowise.core.workspace.diagnostics import (
    ExtractionDiagnostics,
    RepoDiagnostics,
    UnmatchedConsumer,
)
from repowise.core.workspace.system_graph import build_system_graph
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._enrichment import CrossRepoEnricher
from repowise.server.mcp_server.tool_architecture import architecture_payload
from repowise.server.mcp_server.tool_blast_radius import get_blast_radius
from repowise.server.mcp_server.tool_change_risk import _cross_repo_block
from repowise.server.mcp_server.tool_risk.directives import _cross_repo_relationships
from repowise.server.mcp_server.tool_risk.enrichment import _cross_repo_links

PROVIDER_FILE = "src/routes.py"
CONSUMER_FILE = "src/api.ts"
PROVIDER_ID = "http::GET::/api/orders"
CONSUMER_ID = "http::GET::/orders"


def _contracts(*, origins: bool = True) -> list[Contract]:
    return [
        Contract(
            repo="api",
            contract_id=PROVIDER_ID,
            contract_type="http",
            role="provider",
            file_path=PROVIDER_FILE,
            symbol_name="list_orders",
            confidence=0.9,
            symbol_id=f"{PROVIDER_FILE}::list_orders",
            origin="route_registration" if origins else None,
        ),
        Contract(
            repo="web",
            contract_id=CONSUMER_ID,
            contract_type="http",
            role="consumer",
            file_path=CONSUMER_FILE,
            symbol_name="fetchOrders",
            confidence=0.75,
            origin="client_call" if origins else None,
        ),
    ]


def _link(*, strategy: str | None = "prefix_stripped") -> ContractLink:
    return ContractLink(
        contract_id=PROVIDER_ID,
        contract_type="http",
        match_type="candidate",
        confidence=0.6,
        provider_repo="api",
        provider_file=PROVIDER_FILE,
        provider_symbol="list_orders",
        provider_service=None,
        consumer_repo="web",
        consumer_file=CONSUMER_FILE,
        consumer_symbol="fetchOrders",
        consumer_service=None,
        provider_symbol_id=f"{PROVIDER_FILE}::list_orders",
        consumer_contract_id=CONSUMER_ID,
        strategy=strategy,
    )


def _diagnostics(*, legacy: bool = False) -> dict:
    """Two HTTP calls in web: one matching nothing, one served in its own service."""
    dangling = UnmatchedConsumer("web", "src/a.ts", "http::GET::/x", "http", "no_provider")
    in_service = UnmatchedConsumer("web", "src/b.ts", "http::GET::/y", "http", "internal_only")
    diag = ExtractionDiagnostics(
        repo_breakdown=[
            RepoDiagnostics(repo="api", providers_by_type={"http": 1}),
            RepoDiagnostics(repo="web", consumers_by_type={"http": 3}),
        ],
        unmatched_consumers=[dangling],
        in_service_consumers=[in_service],
        http_consumers_linked=1,
    ).to_dict()
    if legacy:
        # Written before in-service calls were split out: listed as unmatched.
        diag["unmatched_consumers"].append(diag.pop("in_service_consumers")[0])
    return diag


def _enricher(
    tmp_path: Path,
    *,
    origins: bool = True,
    strategy: str | None = "prefix_stripped",
    legacy_diagnostics: bool = False,
    unread_repo: bool = True,
) -> CrossRepoEnricher:
    contracts = _contracts(origins=origins)
    links = [_link(strategy=strategy)]
    graph = build_system_graph(contracts, links, CrossRepoOverlay(), {}, generated_at="t")
    raw_graph = {**graph.to_dict(), "diagnostics": _diagnostics(legacy=legacy_diagnostics)}
    # A walked repo with no contracts leaves the evidence partial.
    walked = ("api", "web", "mongo") if unread_repo else ("api", "web")
    raw_graph["repo_provenance"] = {alias: {"head": "h"} for alias in walked}
    (tmp_path / "system_graph.json").write_text(json.dumps(raw_graph), encoding="utf-8")
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


def test_a_link_names_its_strategy_and_each_ends_origin(tmp_path: Path):
    enricher = _enricher(tmp_path)
    link = enricher.contract_links[0]
    # The consumer end is found under its own contract id, not the provider's.
    assert enricher.link_provenance(link, "consumer") == {
        "strategy": "prefix_stripped",
        "origin": "client_call",
    }
    assert enricher.link_provenance(link, "provider") == {
        "strategy": "prefix_stripped",
        "origin": "route_registration",
    }


def test_an_artifact_without_strategy_or_origin_omits_both(tmp_path: Path):
    enricher = _enricher(tmp_path, origins=False, strategy=None)
    assert enricher.link_provenance(enricher.contract_links[0], "consumer") == {}
    with _in_workspace(enricher):
        block = _cross_repo_block("api", [PROVIDER_FILE])
    row = block["consumers"][0]
    assert row["match_type"] == "candidate"
    assert "strategy" not in row and "origin" not in row


@pytest.mark.asyncio
async def test_blast_radius_consumer_rows_carry_strategy_and_origin(tmp_path: Path):
    with _in_workspace(_enricher(tmp_path)):
        payload = await get_blast_radius(targets=[f"{PROVIDER_FILE}::list_orders"])
    row = payload["symbol_targets"][0]["consumers"][0]
    assert (row["strategy"], row["origin"]) == ("prefix_stripped", "client_call")
    assert payload["evidence"]["state"]


def test_change_risk_block_carries_strategy_evidence_and_the_repos_calls(tmp_path: Path):
    with _in_workspace(_enricher(tmp_path)):
        block = _cross_repo_block("api", [PROVIDER_FILE])
    assert block["consumers"][0]["strategy"] == "prefix_stripped"
    assert block["consumers"][0]["origin"] == "client_call"
    assert block["graph_evidence"] == {"state": "partial", "reason": "repos_without_contracts"}
    # api reads no HTTP call, so it has nothing to split.
    assert "http_calls" not in block


def test_get_risk_rows_and_analysis_carry_provenance_and_evidence(tmp_path: Path):
    enricher = _enricher(tmp_path)
    rows = _cross_repo_links(
        "web",
        CONSUMER_FILE,
        [],
        [],
        enricher.get_contract_links_as_consumer("web", CONSUMER_FILE),
        [],
        enricher,
    )
    assert (rows[0]["strategy"], rows[0]["origin"]) == ("prefix_stripped", "route_registration")
    with _in_workspace(enricher):
        analysis = _cross_repo_relationships("web")["analysis"]
    assert analysis["graph_evidence"] == {"state": "partial", "reason": "repos_without_contracts"}
    assert analysis["http_calls"] == {"read": 3, "not_linked": 1, "in_service": 1}


@pytest.mark.parametrize("legacy", [False, True])
def test_in_service_calls_are_counted_apart_from_not_linked(tmp_path: Path, legacy: bool):
    enricher = _enricher(tmp_path, legacy_diagnostics=legacy)
    assert enricher.http_call_split() == {
        "read": 3,
        "linked": 1,
        "not_linked": 1,
        "in_service": 1,
    }
    payload = architecture_payload(enricher.get_architecture_metrics())
    assert payload["http_calls"]["in_service"] == 1


def test_evidence_and_calls_are_absent_without_a_graph():
    enricher = CrossRepoEnricher.from_data(contracts={"contracts": [], "contract_links": []})
    assert enricher.graph_evidence_brief() is None
    assert enricher.http_call_split() is None


def test_evidence_brief_is_core_state_with_its_reason_codes(tmp_path: Path):
    from repowise.core.workspace.graph_evidence import graph_evidence
    from repowise.core.workspace.system_graph import SystemGraph

    enricher = _enricher(tmp_path)
    core = graph_evidence(SystemGraph.from_dict(enricher.get_system_graph()))
    assert enricher.graph_evidence_brief() == {
        "state": core.state,
        "reason": ", ".join(r.code for r in core.reasons),
    }


def test_a_measured_graph_adds_no_evidence_field(tmp_path: Path):
    enricher = _enricher(tmp_path, unread_repo=False)
    assert enricher.graph_evidence_brief() is None
    with _in_workspace(enricher):
        block = _cross_repo_block("api", [PROVIDER_FILE])
        analysis = _cross_repo_relationships("web")["analysis"]
    assert "graph_evidence" not in block
    assert "graph_evidence" not in analysis


def test_unreadable_diagnostics_drop_only_the_counts():
    graph = {"nodes": [], "edges": [], "diagnostics": {"repo_breakdown": [{"repo": 1}], "x": None}}
    graph["diagnostics"]["symbol_identity"] = {"provider": None}  # from_dict cannot read it
    enricher = CrossRepoEnricher.from_data(system_graph=graph)
    assert enricher.http_call_split() is None
    assert enricher.get_diagnostics() is graph["diagnostics"]
