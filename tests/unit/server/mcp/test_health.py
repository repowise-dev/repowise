"""Unit tests for repowise MCP server tools.

Tests all 9 MCP tools using an in-memory SQLite database with pre-populated
test data, mirroring the conftest pattern from the REST API tests.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_health_dashboard(setup_mcp, health_data):
    from repowise.server.mcp_server import get_health

    result = await get_health()
    assert result["mode"] == "dashboard"
    assert result["kpis"]["file_count"] == 2
    assert result["kpis"]["worst_performer_path"] == "src/auth/service.py"
    assert len(result["worst_files"]) == 2
    assert result["worst_files"][0]["file_path"] == "src/auth/service.py"
    assert len(result["top_findings"]) == 4


@pytest.mark.asyncio
async def test_get_health_dashboard_surfaces_maintainability(setup_mcp, health_data):
    """The maintainability pillar is surfaced as a co-equal second signal."""
    from repowise.server.mcp_server import get_health

    result = await get_health()
    # Repo-level KPI headline for the maintainability pillar.
    # NLOC-weighted: (6.0*200 + 9.0*50) / 250 = 6.6.
    assert result["kpis"]["maintainability_average"] == 6.6
    # Per-file metrics carry all three dimension scores.
    worst = result["worst_files"][0]
    assert worst["defect_score"] == 4.5
    assert worst["maintainability_score"] == 6.0
    assert worst["performance_score"] == 9.0
    # Findings are tagged with their home pillar so they can be filtered.
    dims = {f["dimension"] for f in result["top_findings"]}
    assert dims == {"defect", "maintainability", "performance"}


@pytest.mark.asyncio
async def test_get_health_dashboard_surfaces_performance(setup_mcp, health_data):
    """The performance pillar is surfaced as a co-equal third signal."""
    from repowise.server.mcp_server import get_health

    result = await get_health()
    # Repo-level KPI headline for the performance pillar.
    # NLOC-weighted: (9.0*200 + 10.0*50) / 250 = 9.2.
    assert result["kpis"]["performance_average"] == 9.2
    # The perf finding carries its boundary kind + cross-function reachability path.
    perf = [f for f in result["top_findings"] if f["dimension"] == "performance"]
    assert len(perf) == 1
    details = perf[0]["details"]
    assert details["boundary_kind"] == "db"
    assert details["cross_function"] is True
    assert details["path"] == [
        "src/auth/service.py::load_users",
        "src/db/models.py::fetch_one",
    ]


@pytest.mark.asyncio
async def test_get_health_dashboard_surfaces_leverage(setup_mcp, health_data):
    """Leverage view: which files move the NLOC-weighted headline, not just which score low."""
    from repowise.server.mcp_server import get_health

    result = await get_health()
    kpis = result["kpis"]
    # Weighted (4.5*200 + 8.5*50)/250 = 5.3 vs plain mean (4.5 + 8.5)/2 = 6.5:
    # the divergence is the "a big low file holds the headline down" signal.
    assert kpis["average_health"] == 5.3
    assert kpis["average_health_unweighted"] == 6.5
    assert kpis["average_health_weighting"] == "nloc"

    # Per-file leverage: (8 - score) * nloc, 0 once healthy.
    worst = next(m for m in result["worst_files"] if m["file_path"] == "src/auth/service.py")
    assert worst["weighted_deficit"] == 700  # (8.0 - 4.5) * 200
    healthy = next(m for m in result["worst_files"] if m["file_path"] == "src/db/models.py")
    assert healthy["weighted_deficit"] == 0  # score 8.5 >= Healthy floor

    # high_leverage_files excludes healthy files and leads with the biggest drag.
    hi = result["high_leverage_files"]
    assert [m["file_path"] for m in hi] == ["src/auth/service.py"]

    # gap_analysis: net points to move the *average* to 8.0 credits the healthy
    # file's surplus, so it's 8*250 - (4.5*200 + 8.5*50) = 2000 - 1325 = 675,
    # all sitting in one below-target file.
    gap = result["gap_analysis"]
    assert gap["target_score"] == 8.0
    assert gap["weighted_gap_points"] == 675
    assert gap["files_below_target"] == 1
    assert gap["files_to_reach_target"] == 1
    assert gap["files_for_half_gap"] == 1


@pytest.mark.asyncio
async def test_get_health_refactoring_capped_and_leverage_ranked(setup_mcp, health_data):
    """refactoring_plans is bounded by limit and reports the honest total."""
    from repowise.server.mcp_server import get_health

    result = await get_health(include=["refactoring"], limit=5)
    assert "refactoring_plans" in result
    assert len(result["refactoring_plans"]) <= 5
    # Honest truncation signal is always present when refactoring is requested.
    assert "refactoring_plans_total" in result


@pytest.mark.asyncio
async def test_get_health_targeted(setup_mcp, health_data):
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["src/auth/service.py"])
    assert result["mode"] == "targets"
    assert len(result["metrics"]) == 1
    assert result["metrics"][0]["max_ccn"] == 15
    assert result["metrics"][0]["maintainability_score"] == 6.0
    assert result["metrics"][0]["performance_score"] == 9.0
    assert all(f["file_path"] == "src/auth/service.py" for f in result["findings"])


@pytest.mark.asyncio
async def test_get_health_names_unresolved_targets(setup_mcp, health_data):
    """A target that matched nothing is named with a reason, never dropped."""
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["src/auth/service.py", "does/not/exist.py", "module:nope"])
    assert [m["file_path"] for m in result["metrics"]] == ["src/auth/service.py"]
    by_target = {u["target"]: u["reason"] for u in result["unresolved"]}
    assert by_target == {
        "does/not/exist.py": "no_such_path",
        "module:nope": "no_such_module",
    }
    # A bad module name is correctable without a second dashboard round-trip.
    assert set(result["known_modules"]) == {"auth", "db"}


@pytest.mark.asyncio
async def test_get_health_unmatched_module_stays_scoped(setup_mcp, health_data):
    """A module target that resolves to nothing must not become a repo dashboard.

    Falling through to dashboard mode answered a module-scoped question with
    repo-wide numbers, which reads as scoped and is not.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["module:nope"])
    assert result["mode"] == "targets"
    assert result["metrics"] == []
    assert result["findings"] == []
    assert result["unresolved"] == [{"target": "module:nope", "reason": "no_such_module"}]
    # None of the repo-wide blocks leak into a scoped answer.
    assert not {"kpis", "worst_files", "gap_analysis", "distribution"} & set(result)


@pytest.mark.asyncio
async def test_get_health_module_target_resolves_to_its_files(setup_mcp, health_data):
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["module:auth"])
    assert [m["file_path"] for m in result["metrics"]] == ["src/auth/service.py"]
    assert "unresolved" not in result


@pytest.mark.asyncio
async def test_get_health_findings_capped_with_honest_total(setup_mcp, health_data):
    """limit governs findings too, and the total says what was cut."""
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["src/auth/service.py"], limit=2)
    assert len(result["findings"]) == 2
    assert result["findings_total"] == 4

    dash = await get_health(limit=2)
    assert len(dash["top_findings"]) == 2
    assert dash["top_findings_total"] == 4


@pytest.mark.asyncio
async def test_get_health_modules_capped_with_honest_total(setup_mcp, health_data):
    from repowise.server.mcp_server import get_health

    result = await get_health(limit=1)
    assert len(result["modules"]) == 1
    assert result["modules_total"] == 2


@pytest.mark.asyncio
async def test_get_health_suggestion_text_emitted_once_as_legend(setup_mcp, health_data):
    """Suggestion prose is keyed by biomarker type, so it ships once, not per row."""
    from repowise.server.mcp_server import get_health

    result = await get_health(include=["refactoring"])
    rows = result["top_findings"]
    assert rows, "fixture should produce findings"
    assert not any("suggestion" in r for r in rows)
    legend = result["suggestion_legend"]
    # Exactly the types present — every row can be joined, nothing spare.
    assert set(legend) == {r["biomarker_type"] for r in rows}
    assert all(isinstance(v, str) and v for v in legend.values())


@pytest.mark.asyncio
async def test_get_health_dashboard_leads_with_a_directive(setup_mcp, health_data):
    """The dashboard recommends, not just ranks — the D2 gap."""
    from repowise.server.mcp_server import get_health

    d = (await get_health())["directive"]
    # Ranked by weighted_deficit, so the big low-scoring file leads.
    assert d["fix_first"] == "src/auth/service.py"
    assert d["reason"] == "authenticate has cyclomatic complexity 15"
    assert d["recovers_points"] == 700  # (8.0 - 4.5) * 200
    # 700 of the repo's 675 net gap points: the healthy file's surplus is
    # credited in the denominator, so one file can exceed 100%.
    assert d["share_of_repo_gap_pct"] == pytest.approx(103.7)
    assert d["then"] == []  # only one below-target file in the fixture


@pytest.mark.asyncio
async def test_get_health_only_projects_the_response(setup_mcp, health_data):
    """``only`` subtracts blocks; ``include`` could only ever add them."""
    from repowise.server.mcp_server import get_health

    full = await get_health()
    assert len(full) > 4

    slim = await get_health(only=["directive"])
    # mode and _meta always survive — a response you cannot orient in is not
    # a saving.
    assert set(slim) == {"directive", "mode", "_meta"}
    assert slim["directive"] == full["directive"]


@pytest.mark.asyncio
async def test_get_health_only_names_unknown_keys(setup_mcp, health_data):
    """A misspelled projection is named, not silently answered with nothing."""
    from repowise.server.mcp_server import get_health

    result = await get_health(only=["directive", "kpiz"])
    assert result["unknown_only_keys"] == ["kpiz"]
    assert "directive" in result


@pytest.mark.asyncio
async def test_get_health_meta_stamps_when_health_last_ran(setup_mcp, health_data):
    """Health is a separate pass from indexing and can lag it."""
    from repowise.server.mcp_server import get_health

    meta = (await get_health())["_meta"]
    assert isinstance(meta["health_analyzed_at"], str)


@pytest.mark.asyncio
async def test_get_health_accepts_windows_separators(setup_mcp, health_data):
    """A backslash path is the same file, not a no_such_path."""
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["src\\auth\\service.py"])
    assert [m["file_path"] for m in result["metrics"]] == ["src/auth/service.py"]
    assert "unresolved" not in result


@pytest.mark.asyncio
async def test_get_health_metric_carries_dominant_cause_and_magnitude(setup_mcp, health_data):
    """Metric rows lead with the worst finding + the pre-floor deduction sum."""
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["src/auth/service.py"])
    metric = result["metrics"][0]
    # Worst of {complex_method 1.2, nested 0.7, low_cohesion 1.0, io_in_loop 1.0}.
    assert metric["primary_biomarker"] == "complex_method"
    assert metric["primary_reason"] == "authenticate has cyclomatic complexity 15"
    # Σ health_impact = 1.2 + 0.7 + 1.0 + 1.0 — the depth behind a floored score.
    assert metric["total_deduction"] == pytest.approx(3.9)
    # Same lead reaches dashboard worst_files.
    dash = await get_health()
    worst = next(m for m in dash["worst_files"] if m["file_path"] == "src/auth/service.py")
    assert worst["primary_biomarker"] == "complex_method"
    assert worst["total_deduction"] == pytest.approx(3.9)
