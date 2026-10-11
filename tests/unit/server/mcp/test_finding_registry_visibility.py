"""The finding-type registry decides what default surfaces show.

A hidden type never reaches default MCP output; a provisional type appears
only when asked for by name (or via ``include=["unverified"]``), and then
carries ``verification: "unverified"``.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis import finding_registry
from repowise.core.analysis.finding_registry import (
    UNVERIFIED_LABEL,
    FindingTypeStatus,
    excluded_types,
    status_of,
    verification_label,
    withheld_summary,
)


def _set(monkeypatch, name: str, status: str) -> None:
    monkeypatch.setitem(finding_registry.REGISTRY, name, FindingTypeStatus(status, reason="test"))


def _dead_kinds(result: dict) -> list[str]:
    return [f["kind"] for tier in result["tiers"].values() for f in tier["findings"]]


def test_unlisted_type_is_validated():
    assert status_of("no_such_type_anywhere").status == "validated"
    assert verification_label("no_such_type_anywhere") is None


def test_excluded_types_hidden_always_provisional_unless_requested(monkeypatch):
    _set(monkeypatch, "t_hidden", "hidden")
    _set(monkeypatch, "t_prov", "provisional")
    assert {"t_hidden", "t_prov"} <= excluded_types()
    assert "t_prov" not in excluded_types(requested=["t_prov"])
    assert "t_prov" not in excluded_types(include_provisional=True)
    # Naming a hidden type is not enough to show it.
    assert "t_hidden" in excluded_types(requested=["t_hidden"], include_provisional=True)
    assert verification_label("t_prov") == UNVERIFIED_LABEL


def test_withheld_summary_reports_only_types_with_rows(monkeypatch):
    _set(monkeypatch, "t_hidden", "hidden")
    out = withheld_summary({"t_hidden": 4, "other": 2}, {"t_hidden", "t_empty"})
    assert out == {"t_hidden": {"count": 4, "status": "hidden", "reason": "test"}}


@pytest.mark.asyncio
async def test_hidden_dead_code_kind_never_in_default_output(setup_mcp, monkeypatch):
    from repowise.server.mcp_server import get_dead_code

    _set(monkeypatch, "unused_export", "hidden")
    result = await get_dead_code(min_confidence=0.0)
    assert "unused_export" not in _dead_kinds(result)
    assert result["summary"]["total_findings"] == 1
    assert "unused_export" not in result["summary"]["by_kind"]
    assert result["summary"]["withheld_types"]["unused_export"]["count"] == 2

    # Asking for it by name does not bring a hidden kind back.
    named = await get_dead_code(kind="unused_export", min_confidence=0.0)
    assert _dead_kinds(named) == []


@pytest.mark.asyncio
async def test_provisional_dead_code_kind_only_when_named_and_labelled(setup_mcp, monkeypatch):
    from repowise.server.mcp_server import get_dead_code

    _set(monkeypatch, "unused_export", "provisional")
    default = await get_dead_code(min_confidence=0.0)
    assert "unused_export" not in _dead_kinds(default)

    named = await get_dead_code(kind="unused_export", min_confidence=0.0)
    rows = [f for tier in named["tiers"].values() for f in tier["findings"]]
    assert len(rows) == 2
    assert all(f["verification"] == UNVERIFIED_LABEL for f in rows)
    # A validated kind carries no label.
    files = await get_dead_code(kind="unreachable_file", min_confidence=0.0)
    assert all(
        "verification" not in f for tier in files["tiers"].values() for f in tier["findings"]
    )


@pytest.mark.asyncio
async def test_hidden_health_type_never_in_default_output(setup_mcp, health_data, monkeypatch):
    from repowise.server.mcp_server import get_health

    _set(monkeypatch, "complex_method", "hidden")
    dashboard = await get_health(only=["top_findings", "test_findings"], limit=50)
    types = [f["biomarker_type"] for f in dashboard["top_findings"]]
    assert types and "complex_method" not in types

    targeted = await get_health(targets=["src/auth/service.py"], include=["unverified"])
    listed = [f["biomarker_type"] for f in targeted.get("findings", [])]
    assert listed and "complex_method" not in listed
    # Nor does it lead a file.
    for row in targeted.get("metrics", []):
        assert row.get("primary_biomarker") != "complex_method"


@pytest.mark.asyncio
async def test_provisional_health_type_only_when_opted_in(setup_mcp, health_data, monkeypatch):
    from repowise.server.mcp_server import get_health

    _set(monkeypatch, "nested_complexity", "provisional")
    default = await get_health(only=["top_findings"], limit=50)
    assert "nested_complexity" not in [f["biomarker_type"] for f in default["top_findings"]]

    opted = await get_health(only=["top_findings"], include=["unverified"], limit=50)
    rows = [f for f in opted["top_findings"] if f["biomarker_type"] == "nested_complexity"]
    assert rows and all(f["verification"] == UNVERIFIED_LABEL for f in rows)
    assert all(
        "verification" not in f
        for f in opted["top_findings"]
        if f["biomarker_type"] != "nested_complexity"
    )
    assert "unknown_include_keys" not in opted


@pytest.mark.asyncio
async def test_crud_reads_leave_withheld_types_out(session, health_data, monkeypatch):
    from repowise.core.persistence.crud import get_dead_code_findings, get_health_findings

    _set(monkeypatch, "complex_method", "hidden")
    _set(monkeypatch, "unused_export", "hidden")
    shown = {f.biomarker_type for f in await get_health_findings(session, health_data)}
    assert "complex_method" not in shown and shown
    every = {
        f.biomarker_type
        for f in await get_health_findings(session, health_data, include_withheld=True)
    }
    assert "complex_method" in every
    kinds = {f.kind for f in await get_dead_code_findings(session, health_data)}
    assert kinds == {"unreachable_file"}
