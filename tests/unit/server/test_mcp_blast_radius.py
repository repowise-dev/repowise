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
        ),
        Contract(
            repo="frontend",
            contract_id="http::GET::/users",
            contract_type="http",
            role="consumer",
            file_path="client.ts",
            symbol_name="fetchUsers",
            confidence=0.8,
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
    return CrossRepoEnricher(
        tmp_path / "cross_repo_edges.json",
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
    # No cross-repo co-change edges in this fixture.
    assert missing_cochanges == []


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
        directive = _breaking_change_directive("backend")
    finally:
        _state._registry = prev_registry
        _state._cross_repo_enricher = prev_enricher
    assert len(directive) == 1
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
        assert _breaking_change_directive("frontend") == []
    finally:
        _state._registry = prev_registry
        _state._cross_repo_enricher = prev_enricher


def test_breaking_change_directive_empty_outside_workspace():
    from repowise.server.mcp_server.tool_risk import _breaking_change_directive

    prev = _state._registry
    _state._registry = None
    try:
        assert _breaking_change_directive("backend") == []
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
