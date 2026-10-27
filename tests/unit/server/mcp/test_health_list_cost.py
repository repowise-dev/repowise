"""A get_health call pays only for the blocks it returns.

Tiering every open finding and building the whole dashboard cost a list call on
a large repository most of its time, then were projected away. These pin that a
call which prints no finding never tiers one, and that what it prints is
unchanged.
"""

from __future__ import annotations

import pytest

import repowise.server.mcp_server.tool_health.dashboard as dashboard_mod
import repowise.server.mcp_server.tool_health.findings as findings_mod


def _forbid(monkeypatch, module, name: str) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError(f"{name} ran for a call that does not print it")

    monkeypatch.setattr(module, name, boom)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("args", "kept"),
    [
        ({}, "kpis"),
        ({"only": ["fix_first"]}, "fix_first"),
        (
            {"include": ["performance"], "only": ["performance_opportunities"]},
            "performance_opportunities",
        ),
        ({"include": ["refactoring"], "only": ["refactoring_opportunities"]}, "mode"),
    ],
)
async def test_a_call_that_prints_no_finding_never_tiers_one(
    setup_mcp, health_data, monkeypatch, args, kept
):
    from repowise.server.mcp_server import get_health
    from repowise.server.mcp_server.tool_health.request import HealthRequest

    # The payload every call had when it always tiered: totals, secondary
    # rankings and every kept block must match it.
    with monkeypatch.context() as always:
        always.setattr(HealthRequest, "ranks_findings", property(lambda _self: True))
        expected = await get_health(**args)
    _forbid(monkeypatch, findings_mod, "finding_priorities")
    result = await get_health(**args)
    assert kept in result
    result["_meta"].pop("timing_ms", None)
    expected["_meta"].pop("timing_ms", None)
    assert result == expected


@pytest.mark.asyncio
async def test_a_list_call_builds_no_dashboard_block(setup_mcp, health_data, monkeypatch):
    from repowise.server.mcp_server import get_health

    for name in ("_compute_kpis", "_module_rollups", "health_distribution", "_gap_analysis"):
        _forbid(monkeypatch, dashboard_mod, name)
    result = await get_health(only=["top_findings"])
    assert result["top_findings"]


@pytest.mark.asyncio
async def test_printed_findings_keep_their_tier(setup_mcp, health_data):
    from repowise.server.mcp_server import get_health

    listed = await get_health(only=["top_findings"], limit=50)
    with_biomarkers = await get_health(include=["biomarkers"], only=["findings"], limit=50)
    assert [f["id"] for f in listed["top_findings"]] == [
        f["id"] for f in with_biomarkers["findings"]
    ]


@pytest.mark.asyncio
async def test_no_plan_status_names_the_first_ranked_finding(setup_mcp, health_data):
    """With no plan stored, the plans status points at the head of the ranked
    list, which the plans call ranks on demand since it prints no finding."""
    from repowise.server.mcp_server import get_health

    plans = await get_health(include=["refactoring"], only=["refactoring_plans"])
    status = plans["refactoring_plans_status"]
    assert status["state"] == "indeterminate"
    (head,) = (await get_health(only=["top_findings"], limit=1))["top_findings"]
    symbol = status["next_action"]["arguments"]["symbol_id"]
    assert symbol == f"{head['file_path']}:{head['line_start']}-{head['line_end']}"
