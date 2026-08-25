"""Tests for the get_blast_radius MCP tool (workspace-only cross-repo impact)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.workspace.contracts import Contract, ContractLink
from repowise.core.workspace.cross_repo import CrossRepoOverlay, CrossRepoPackageDep
from repowise.core.workspace.system_graph import build_system_graph
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._enrichment import CrossRepoEnricher
from repowise.server.mcp_server.tool_blast_radius import get_blast_radius


def _enricher_with_graph(tmp_path: Path) -> CrossRepoEnricher:
    contracts = [
        Contract(
            repo="backend",
            contract_id="http::GET::/users",
            contract_type="http",
            role="provider",
            file_path="routes.py",
            symbol_name="get_users",
            confidence=0.9,
            symbol_id="routes.py::get_users",
        ),
        Contract(
            repo="frontend",
            contract_id="http::GET::/users",
            contract_type="http",
            role="consumer",
            file_path="client.ts",
            symbol_name="fetchUsers",
            confidence=0.8,
            symbol_id="client.ts::fetchUsers",
        ),
    ]
    links = [
        ContractLink(
            contract_id="http::GET::/users",
            contract_type="http",
            match_type="exact",
            confidence=0.8,
            provider_repo="backend",
            provider_file="routes.py",
            provider_symbol="get_users",
            provider_service=None,
            consumer_repo="frontend",
            consumer_file="client.ts",
            consumer_symbol="fetchUsers",
            consumer_service=None,
            provider_symbol_id="routes.py::get_users",
            consumer_symbol_id="client.ts::fetchUsers",
        ),
    ]
    overlay = CrossRepoOverlay(
        package_deps=[
            CrossRepoPackageDep(
                source_repo="frontend",
                target_repo="backend",
                source_manifest="package.json",
                kind="npm_local_path",
            ),
        ]
    )
    graph = build_system_graph(contracts, links, overlay, {}, generated_at="t")
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


@pytest.fixture
def workspace_state(tmp_path: Path):
    """Enable workspace mode with a real system-graph-backed enricher."""
    prev_registry = _state._registry
    prev_enricher = _state._cross_repo_enricher
    _state._registry = object()  # _is_workspace_mode() only checks for non-None
    _state._cross_repo_enricher = _enricher_with_graph(tmp_path)
    try:
        yield
    finally:
        _state._registry = prev_registry
        _state._cross_repo_enricher = prev_enricher


@pytest.mark.asyncio
async def test_requires_workspace_mode():
    prev = _state._registry
    _state._registry = None
    try:
        result = await get_blast_radius(["backend"])
    finally:
        _state._registry = prev
    assert "error" in result
    assert "workspace mode" in result["error"]


@pytest.mark.asyncio
async def test_changing_provider_impacts_consumer(workspace_state):
    result = await get_blast_radius(["backend"])
    ids = {n["id"] for n in result["impacted"]}
    assert "frontend" in ids
    assert result["structural_count"] >= 1
    assert "frontend" in result["impacted_repos"]
    assert "backend" in result["targets"]
    assert "downstream service" in result["summary"]
    semantics = result["impact_score_semantics"]
    assert semantics["field"] == "impacted[].score"
    assert semantics["unit"] == "relative_weight"
    assert semantics["calibration"]["status"] == "uncalibrated"
    assert semantics["runtime_breakage_probability"] is False


@pytest.mark.asyncio
async def test_unresolved_target(workspace_state):
    result = await get_blast_radius(["ghost"])
    assert result["unresolved_targets"] == ["ghost"]
    assert result["impacted"] == []
    assert result["targets"] == []


@pytest.mark.asyncio
async def test_leaf_consumer_has_no_downstream(workspace_state):
    result = await get_blast_radius(["frontend"])
    # frontend only consumes / depends; nothing downstream of it.
    assert result["impacted"] == []
    assert result["total_impacted"] == 0


def test_cross_repo_directive_splits_structural_and_behavioral(workspace_state):
    """The get_risk PR-mode cross-repo helper reports other-repo consumers."""
    from repowise.server.mcp_server.tool_risk import _cross_repo_directive

    will_break, missing_cochanges = _cross_repo_directive("backend")
    # frontend consumes backend (http) and package-depends on it → structural.
    assert any(e["repo"] == "frontend" for e in will_break)
    assert all("service" in e and "score" in e for e in will_break)
    assert all(e["relationship_type"] == "structural_dependency" for e in will_break)
    assert all(e["direction"] == "consumer_to_dependency" for e in will_break)
    assert all(e["claim"] == "structural_reach" for e in will_break)
    assert all(e["runtime_breakage_claim"] is False for e in will_break)
    assert all(e["consumer_repository"] != e["dependency_repository"] for e in will_break)
    # No cross-repo co-change edges in this fixture.
    assert missing_cochanges == []

    from repowise.server.mcp_server.tool_risk.directives import _cross_repo_relationships

    relationships = _cross_repo_relationships("backend")
    assert relationships["structural_total"] == len(will_break)
    assert len(relationships["structural"]) <= relationships["structural_total"]
    assert relationships["analysis"]["status"] == "partial"
    assert relationships["analysis"]["evidence_resolution"] == "aggregated_path_edge_kinds"
    assert relationships["analysis"]["generated_at"] == "t"
    assert relationships["analysis"]["freshness"]["status"] == "unavailable"


def test_cross_repo_directive_empty_outside_workspace():
    from repowise.server.mcp_server import _state
    from repowise.server.mcp_server.tool_risk import _cross_repo_directive

    prev = _state._registry
    _state._registry = None
    try:
        assert _cross_repo_directive("backend") == ([], [])
    finally:
        _state._registry = prev


def _enricher_with_breaking(tmp_path: Path) -> CrossRepoEnricher:
    from repowise.core.workspace.breaking_change import detect_breaking_changes
    from repowise.core.workspace.contracts import ContractStore

    prev = ContractStore(
        contracts=[
            Contract(
                repo="backend",
                contract_id="http::GET::/users",
                contract_type="http",
                role="provider",
                file_path="routes.py",
                symbol_name="get_users",
                confidence=0.9,
            ),
        ],
        contract_links=[
            ContractLink(
                contract_id="http::GET::/users",
                contract_type="http",
                match_type="exact",
                confidence=0.8,
                provider_repo="backend",
                provider_file="routes.py",
                provider_symbol="get_users",
                provider_service=None,
                consumer_repo="frontend",
                consumer_file="client.ts",
                consumer_symbol="fetchUsers",
                consumer_service=None,
            ),
        ],
    )
    report = detect_breaking_changes(prev, ContractStore(), generated_at="t")
    (tmp_path / "breaking_changes.json").write_text(json.dumps(report.to_dict()), encoding="utf-8")
    return CrossRepoEnricher(
        tmp_path / "cross_repo_edges.json",
        breaking_changes_path=tmp_path / "breaking_changes.json",
    )


def test_breaking_change_directive_reports_impacted_consumers(tmp_path: Path):
    from repowise.server.mcp_server.tool_risk import _breaking_change_directive

    prev_registry = _state._registry
    prev_enricher = _state._cross_repo_enricher
    _state._registry = object()
    _state._cross_repo_enricher = _enricher_with_breaking(tmp_path)
    try:
        directive, dropped = _breaking_change_directive("backend")
    finally:
        _state._registry = prev_registry
        _state._cross_repo_enricher = prev_enricher
    assert len(directive) == 1
    assert dropped == 0
    assert directive[0]["kind"] == "removed_endpoint"
    assert directive[0]["severity"] == "breaking"
    assert directive[0]["impacted_consumers"][0]["repo"] == "frontend"


def test_breaking_change_directive_empty_for_other_repo(tmp_path: Path):
    from repowise.server.mcp_server.tool_risk import _breaking_change_directive

    prev_registry = _state._registry
    prev_enricher = _state._cross_repo_enricher
    _state._registry = object()
    _state._cross_repo_enricher = _enricher_with_breaking(tmp_path)
    try:
        # 'frontend' is a consumer, not the provider of the change → no directive.
        assert _breaking_change_directive("frontend") == ([], 0)
    finally:
        _state._registry = prev_registry
        _state._cross_repo_enricher = prev_enricher


def test_breaking_change_directive_empty_outside_workspace():
    from repowise.server.mcp_server.tool_risk import _breaking_change_directive

    prev = _state._registry
    _state._registry = None
    try:
        assert _breaking_change_directive("backend") == ([], 0)
    finally:
        _state._registry = prev


@pytest.mark.asyncio
async def test_no_system_graph_returns_error(tmp_path: Path):
    prev_registry = _state._registry
    prev_enricher = _state._cross_repo_enricher
    _state._registry = object()
    _state._cross_repo_enricher = CrossRepoEnricher(tmp_path / "cross_repo_edges.json")
    try:
        result = await get_blast_radius(["backend"])
    finally:
        _state._registry = prev_registry
        _state._cross_repo_enricher = prev_enricher
    assert "error" in result
    assert "system graph" in result["error"]


@pytest.mark.asyncio
async def test_symbol_target_resolves_to_publishing_service(workspace_state):
    """A symbol id names the service that publishes it, not a node id."""
    result = await get_blast_radius(["routes.py::get_users"])
    assert result["targets"] == ["backend"]
    assert result["unresolved_targets"] == []
    assert {n["id"] for n in result["impacted"]} == {"frontend"}


@pytest.mark.asyncio
async def test_symbol_target_names_the_consuming_symbol(workspace_state):
    """The hop the service graph cannot express: which symbol calls it."""
    result = await get_blast_radius(["routes.py::get_users"])
    blocks = result["symbol_targets"]
    assert len(blocks) == 1
    assert blocks[0]["symbol_id"] == "routes.py::get_users"
    assert blocks[0]["nodes"] == ["backend"]
    assert blocks[0]["contract_ids"] == ["http::GET::/users"]
    assert blocks[0]["consumers"] == [
        {
            "provider_repo": "backend",
            "repo": "frontend",
            "file": "client.ts",
            "contract_id": "http::GET::/users",
            "contract_type": "http",
            "match_type": "exact",
            "confidence": 0.8,
            "symbol_id": "client.ts::fetchUsers",
        }
    ]
    assert blocks[0]["consumers_truncated"] == 0
    assert blocks[0]["consumer_count"] == 1
    assert "ambiguous_in_repos" not in blocks[0]
    assert result["summary"].endswith(
        " 1 symbol target(s) have 1 consumer(s) across the contract links."
    )


@pytest.mark.asyncio
async def test_unknown_symbol_id_stays_unresolved(workspace_state):
    """A symbol-shaped string bound to no contract is not silently absorbed."""
    result = await get_blast_radius(["routes.py::no_such_handler"])
    assert result["unresolved_targets"] == ["routes.py::no_such_handler"]
    assert result["targets"] == []
    assert "symbol_targets" not in result


@pytest.mark.asyncio
async def test_node_target_carries_no_symbol_block(workspace_state):
    """Existing node/alias targets keep their exact previous response shape."""
    result = await get_blast_radius(["backend"])
    assert "symbol_targets" not in result


def _breaking_enricher(tmp_path: Path, contracts, links) -> CrossRepoEnricher:
    """An enricher whose report is 'every one of these providers was removed'."""
    from repowise.core.workspace.breaking_change import detect_breaking_changes
    from repowise.core.workspace.contracts import ContractStore

    report = detect_breaking_changes(
        ContractStore(contracts=contracts, contract_links=links),
        ContractStore(),
        generated_at="t",
    )
    (tmp_path / "breaking_changes.json").write_text(json.dumps(report.to_dict()), encoding="utf-8")
    return CrossRepoEnricher(
        tmp_path / "cross_repo_edges.json",
        breaking_changes_path=tmp_path / "breaking_changes.json",
    )


def _code_provider(name: str) -> Contract:
    return Contract(
        repo="backend",
        contract_id=f"code::@acme/types::{name}",
        contract_type="code",
        role="provider",
        file_path="src/types.ts",
        symbol_name=name,
        confidence=0.9,
        service="packages/types",
        symbol_id=f"src/types.ts::{name}",
    )


def _code_link(name: str) -> ContractLink:
    return ContractLink(
        contract_id=f"code::@acme/types::{name}",
        contract_type="code",
        match_type="exact",
        confidence=0.9,
        provider_repo="backend",
        provider_file="src/types.ts",
        provider_symbol=name,
        provider_service="packages/types",
        consumer_repo="frontend",
        consumer_file="src/api.ts",
        consumer_symbol=f"@acme/types:{name}",
        consumer_service=None,
        provider_symbol_id=f"src/types.ts::{name}",
    )


def _run_directive(tmp_path: Path, contracts, links, alias: str = "backend"):
    from repowise.server.mcp_server.tool_risk import _breaking_change_directive

    prev_registry = _state._registry
    prev_enricher = _state._cross_repo_enricher
    _state._registry = object()
    _state._cross_repo_enricher = _breaking_enricher(tmp_path, contracts, links)
    try:
        return _breaking_change_directive(alias)
    finally:
        _state._registry = prev_registry
        _state._cross_repo_enricher = prev_enricher


def test_directive_carries_code_contracts_with_a_traversable_provider(tmp_path: Path):
    """A removed published symbol is a cross-repo break like a removed route."""
    directive, dropped = _run_directive(tmp_path, [_code_provider("Order")], [_code_link("Order")])
    assert dropped == 0
    assert len(directive) == 1
    entry = directive[0]
    assert entry["type"] == "code"
    assert entry["kind"] == "removed_endpoint"
    assert entry["provider_file"] == "src/types.ts"
    assert entry["provider_symbol_id"] == "src/types.ts::Order"
    assert entry["impacted_consumers"] == [
        {"repo": "frontend", "service": None, "file": "src/api.ts"}
    ]


def test_directive_reports_what_the_provider_cap_left_out(tmp_path: Path):
    """A shared-package bump exceeds the cap; the count must not go silent."""
    names = [f"Type{i}" for i in range(7)]
    directive, dropped = _run_directive(
        tmp_path, [_code_provider(n) for n in names], [_code_link(n) for n in names]
    )
    assert len(directive) == 5
    assert dropped == 2
