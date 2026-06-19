"""Tests for the get_conformance MCP tool + the get_risk conformance directive."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.workspace.config import ConformanceRule
from repowise.core.workspace.conformance import build_conformance_report
from repowise.core.workspace.system_graph import SystemEdge, SystemGraph, SystemNode
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._enrichment import CrossRepoEnricher
from repowise.server.mcp_server.tool_conformance import get_conformance


def _graph() -> SystemGraph:
    return SystemGraph(
        nodes=[
            SystemNode(id="frontend", repo="frontend", service_path=None, name="frontend"),
            SystemNode(id="db", repo="db", service_path=None, name="db"),
        ],
        edges=[
            SystemEdge(
                id="frontend->db:http",
                source="frontend",
                target="db",
                kind="http",
                match_type="exact",
                confidence=1.0,
                weight=1,
                structural=True,
            ),
            SystemEdge(
                id="db->frontend:http",
                source="db",
                target="frontend",
                kind="http",
                match_type="exact",
                confidence=1.0,
                weight=1,
                structural=True,
            ),
        ],
    )


def _enricher_with_conformance(tmp_path: Path) -> CrossRepoEnricher:
    report = build_conformance_report(
        _graph(), [ConformanceRule(source="frontend", target="db")], generated_at="t"
    )
    (tmp_path / "conformance.json").write_text(json.dumps(report.to_dict()), encoding="utf-8")
    return CrossRepoEnricher(
        tmp_path / "cross_repo_edges.json",
        conformance_path=tmp_path / "conformance.json",
    )


@pytest.fixture
def workspace_state(tmp_path: Path):
    prev_registry = _state._registry
    prev_enricher = _state._cross_repo_enricher
    _state._registry = object()
    _state._cross_repo_enricher = _enricher_with_conformance(tmp_path)
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
        result = await get_conformance()
    finally:
        _state._registry = prev
    assert "error" in result
    assert "workspace mode" in result["error"]


@pytest.mark.asyncio
async def test_reports_violations_and_cycles(workspace_state):
    result = await get_conformance()
    assert result["violation_count"] == 1
    assert result["cycle_count"] == 1
    assert result["violations"][0]["source"] == "frontend"
    assert "violation" in result["summary"]


@pytest.mark.asyncio
async def test_filter_by_repo(workspace_state):
    result = await get_conformance(repo="unrelated")
    assert result["violation_count"] == 0
    assert result["cycle_count"] == 0


@pytest.mark.asyncio
async def test_no_report_returns_error(tmp_path: Path):
    prev_registry = _state._registry
    prev_enricher = _state._cross_repo_enricher
    _state._registry = object()
    _state._cross_repo_enricher = CrossRepoEnricher(tmp_path / "cross_repo_edges.json")
    try:
        result = await get_conformance()
    finally:
        _state._registry = prev_registry
        _state._cross_repo_enricher = prev_enricher
    assert "error" in result
    assert "conformance" in result["error"].lower()


# ---------------------------------------------------------------------------
# get_risk PR-mode conformance directive
# ---------------------------------------------------------------------------


def test_conformance_directive_reports_repo_findings(tmp_path: Path):
    from repowise.server.mcp_server.tool_risk import _conformance_directive

    prev_registry = _state._registry
    prev_enricher = _state._cross_repo_enricher
    _state._registry = object()
    _state._cross_repo_enricher = _enricher_with_conformance(tmp_path)
    try:
        violations, cycles = _conformance_directive("frontend")
    finally:
        _state._registry = prev_registry
        _state._cross_repo_enricher = prev_enricher
    assert len(violations) == 1
    assert violations[0]["source"] == "frontend"
    assert violations[0]["rule"] == "frontend !-> db"
    # frontend participates in the frontend<->db cycle.
    assert len(cycles) == 1


def test_conformance_directive_empty_for_unrelated_repo(tmp_path: Path):
    from repowise.server.mcp_server.tool_risk import _conformance_directive

    prev_registry = _state._registry
    prev_enricher = _state._cross_repo_enricher
    _state._registry = object()
    _state._cross_repo_enricher = _enricher_with_conformance(tmp_path)
    try:
        violations, cycles = _conformance_directive("unrelated")
    finally:
        _state._registry = prev_registry
        _state._cross_repo_enricher = prev_enricher
    assert violations == []
    assert cycles == []


def test_conformance_directive_empty_outside_workspace():
    from repowise.server.mcp_server.tool_risk import _conformance_directive

    prev = _state._registry
    _state._registry = None
    try:
        assert _conformance_directive("frontend") == ([], [])
    finally:
        _state._registry = prev
