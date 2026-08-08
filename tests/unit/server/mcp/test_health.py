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


@pytest.mark.asyncio
async def test_get_health_biomarkers_block_is_capped(setup_mcp, health_data):
    """``include=["biomarkers"]`` respects ``limit`` and reports the true total.

    Regression: this was the one ranked list in the tool with no cap, so a
    dashboard-mode call returned every open finding in the repo — 10.3k rows /
    4.7MB on repowise itself, which overflows an agent's context and yields
    nothing usable. Every other list caps and carries a ``*_total`` sibling.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health(include=["biomarkers"], limit=2)
    assert len(result["findings"]) == 2
    # The cap is visible rather than inferred from the length.
    assert result["findings_total"] == 4
    # Impact-ordered, so the cap keeps the findings worth reading.
    impacts = [f["health_impact"] for f in result["findings"]]
    assert impacts == sorted(impacts, reverse=True)


@pytest.mark.asyncio
async def test_get_health_totals_survive_the_cap(setup_mcp, health_data):
    """Totals count the whole open set even when only ``limit`` rows ship.

    Dashboard mode no longer hydrates every finding to emit a handful, so the
    totals come from a separate narrow read; this pins them to the full set
    rather than the truncated head.
    """
    from repowise.server.mcp_server import get_health

    capped = await get_health(limit=1)
    assert len(capped["top_findings"]) == 1
    assert capped["top_findings_total"] == 4

    scoped = await get_health(targets=["src/auth/service.py"], limit=1)
    assert len(scoped["findings"]) == 1
    assert scoped["findings_total"] == 4


@pytest.mark.asyncio
async def test_get_health_only_projection_preserves_block_content(setup_mcp, health_data):
    """``only`` gates the work behind a block without changing what it holds.

    The projection now skips expensive optional work rather than computing and
    discarding it, so the surviving block must still be byte-identical to the
    one the full response carries.
    """
    from repowise.server.mcp_server import get_health

    full = await get_health()
    projected = await get_health(only=["kpis"])
    assert set(projected) == {"mode", "kpis", "_meta"}
    assert projected["kpis"] == full["kpis"]


@pytest.mark.asyncio
async def test_get_health_dimension_filter_is_not_defeated_by_the_cap(setup_mcp, health_data):
    """A dimension filter selects the rows, rather than trimming a capped head.

    Regression: the filter used to run over the finished response, so it
    narrowed a list already capped by ``health_impact``. Performance findings
    carry low impact by construction, so the head was defect-heavy and
    ``include=["biomarkers", "performance"]`` filtered down to nothing — while
    ``findings_total`` still reported the whole repo, which reads as "no
    performance risk here" rather than "none shown".
    """
    from repowise.server.mcp_server import get_health

    # limit=1 forces the cap to bite before the filter would have run.
    result = await get_health(include=["biomarkers", "performance"], limit=1)
    assert [f["dimension"] for f in result["findings"]] == ["performance"]
    # The total describes the filtered set, so an empty list is unambiguous.
    assert result["findings_total"] == 1

    maint = await get_health(include=["biomarkers", "maintainability"], limit=1)
    assert [f["dimension"] for f in maint["findings"]] == ["maintainability"]

    scoped = await get_health(
        targets=["src/auth/service.py"], include=["biomarkers", "performance"]
    )
    assert [f["dimension"] for f in scoped["findings"]] == ["performance"]


@pytest.mark.asyncio
async def test_get_health_dimension_filter_leaves_kpis_and_ranking_alone(setup_mcp, health_data):
    """Asking to *see* one dimension must not restate the repo's health.

    The leads and the performance KPI come from the unfiltered open set; only
    the emitted findings narrow.
    """
    from repowise.server.mcp_server import get_health

    full = await get_health()
    filtered = await get_health(include=["biomarkers", "maintainability"])
    assert filtered["kpis"]["performance_findings"] == full["kpis"]["performance_findings"]
    assert filtered["worst_files"] == full["worst_files"]


@pytest.fixture
async def floored_health_data(session, populated_db: str) -> str:
    """Four files clamped at the 1.0 score floor, worst one last by path.

    Mirrors the shape of a real repo, where 30 files sit at exactly 1.0 and the
    deepest of them sorts last alphabetically.
    """
    from repowise.core.persistence.crud import save_health_findings, save_health_metrics

    rid = populated_db
    paths = ["src/a.py", "src/b.py", "src/c.py", "src/z.py"]
    await save_health_metrics(
        session,
        rid,
        [
            {
                "file_path": p,
                "score": 1.0,
                "max_ccn": 20,
                "max_nesting": 5,
                "nloc": 100,
                "has_test_file": False,
                "module": "src",
            }
            for p in paths
        ],
    )
    # z.py is the deepest by a wide margin and would be invisible under a
    # score-only sort at any limit below 4.
    impacts = {"src/a.py": 2.0, "src/b.py": 3.0, "src/c.py": 4.0, "src/z.py": 9.0}
    await save_health_findings(
        session,
        rid,
        [
            {
                "file_path": p,
                "biomarker_type": "complex_method",
                "severity": "high",
                "function_name": "run",
                "line_start": 1,
                "line_end": 20,
                "details": {},
                "health_impact": impact,
                "reason": f"{p} is complex",
            }
            for p, impact in impacts.items()
        ],
    )
    return rid


@pytest.mark.asyncio
async def test_worst_files_ranks_floored_ties_by_deduction(setup_mcp, floored_health_data):
    """The headline "worst files" list contains the actual worst file.

    Regression: the list sorted on ``score`` alone. The score clamps at 1.0, so
    ties broke by DB order — path order in practice — and on this repo the file
    carrying the largest deduction landed at position 27 of a list capped at 20.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health()
    assert [m["file_path"] for m in result["worst_files"]] == [
        "src/z.py",
        "src/c.py",
        "src/b.py",
        "src/a.py",
    ]

    # The cap is where the old order actually hurt: under a score-only sort the
    # worst file falls off the page entirely.
    capped = await get_health(limit=1)
    assert [m["file_path"] for m in capped["worst_files"]] == ["src/z.py"]


@pytest.mark.asyncio
async def test_worst_files_order_survives_every_dimension_filter(setup_mcp, floored_health_data):
    """Ranking reads the unfiltered open set, whatever the caller asked to see.

    The combination is the point: a cap and a filter interacting is where this
    tool has broken twice. Narrowing the *findings* to one dimension must not
    silently re-rank which files the repo's worst are.
    """
    from repowise.server.mcp_server import get_health

    baseline = [m["file_path"] for m in (await get_health(limit=2))["worst_files"]]
    assert baseline == ["src/z.py", "src/c.py"]

    for include in (["defect"], ["maintainability"], ["performance"], ["biomarkers", "defect"]):
        result = await get_health(include=include, limit=2)
        assert [m["file_path"] for m in result["worst_files"]] == baseline, include


@pytest.mark.asyncio
async def test_one_response_agrees_with_itself_about_the_worst_file(
    setup_mcp, floored_health_data
):
    """``kpis`` and ``worst_files`` must name the same file.

    Regression: ``_compute_kpis`` reduces with ``min()``, which returns the
    *first* minimum, so it answered from whatever order it was handed. Ranking
    ``worst_files`` without also ranking the list behind the KPIs produced a
    payload whose headline named one file as the worst performer while the
    list printed directly beneath it led with another.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health()
    assert result["kpis"]["worst_performer_path"] == result["worst_files"][0]["file_path"]
    assert result["kpis"]["worst_performer_path"] == "src/z.py"

    # Same reduction, same tie, one level down: the module rollup's worst
    # performer is picked with ``min()`` over the same rows.
    assert [m["worst_performer_path"] for m in result["modules"]] == ["src/z.py"]
