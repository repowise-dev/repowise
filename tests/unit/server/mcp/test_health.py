"""Unit tests for repowise MCP server tools.

Tests all 9 MCP tools using an in-memory SQLite database with pre-populated
test data, mirroring the conftest pattern from the REST API tests.
"""

from __future__ import annotations

import json

import pytest

from repowise.core.persistence.models import HealthFinding


@pytest.mark.asyncio
async def test_get_health_dashboard(setup_mcp, health_data):
    from repowise.server.mcp_server import get_health

    result = await get_health()
    assert result["mode"] == "dashboard"
    assert result["kpis"]["file_count"] == 2
    assert result["kpis"]["worst_performer_path"] == "src/auth/service.py"
    # Three leads, all first: the defect one, and one per pillar that carries
    # no defect impact and so never competed for it.
    assert list(result)[:4] == [
        "directive",
        "refactoring_directive",
        "performance_directive",
        "mode",
    ]
    assert "high_leverage_files" in result
    assert "worst_files" not in result
    assert result["secondary_rankings"]["worst_files"]["total"] == 2
    # Three, not four: the impact ranking carries defect and maintainability
    # work. The performance finding scores zero impact by construction, and a
    # row that cannot rank is not a rank. It leads the response instead, in
    # `performance_directive`.
    assert result["secondary_rankings"]["top_findings"]["total"] == 3
    assert "omitted" not in result["_meta"]


@pytest.mark.asyncio
async def test_finding_ids_distinguish_same_coordinate_evidence(
    setup_mcp, health_data, session
):
    from repowise.server.mcp_server import get_health

    rows = [
        HealthFinding(
            id=f"storage-{partner}",
            repository_id=health_data,
            file_path="src/auth/service.py",
            biomarker_type="hidden_coupling",
            severity="medium",
            function_name=None,
            line_start=None,
            line_end=None,
            details_json=json.dumps({"partner": partner}),
            health_impact=0.4,
            reason=f"Changes with {partner}",
            status="open",
        )
        for partner in ("src/a.py", "src/b.py")
    ]
    session.add_all(rows)
    await session.commit()

    emitted = await get_health(only=["top_findings"], limit=10)
    findings = [
        row
        for row in emitted["top_findings"]
        if row["biomarker_type"] == "hidden_coupling"
    ]
    assert len({row["id"] for row in findings}) == 2
    for finding in findings:
        resolved = await get_health(finding_id=finding["id"])
        assert resolved["finding"] == finding


@pytest.mark.asyncio
async def test_dashboard_zero_limit_has_exact_ranked_page_recovery(setup_mcp, health_data):
    from repowise.server.mcp_server import get_health

    result = await get_health(limit=0)
    recovery = result["recovery"]["high_leverage_files"]
    assert "only=['high_leverage_files']" in recovery["call"]
    assert "cursor=0" in recovery["call"]
    assert "limit=1" in recovery["call"]
    recovered = await get_health(only=["high_leverage_files"], limit=1, cursor=0)
    assert len(recovered["high_leverage_files"]) == 1
    assert recovered["high_leverage_files"][0]["file_path"] == "src/auth/service.py"


@pytest.mark.asyncio
async def test_cursor_beyond_end_offers_one_call_reset(setup_mcp, health_data):
    from repowise.server.mcp_server import get_health

    result = await get_health(
        targets=["module:auth"], only=["metrics"], limit=1, cursor=99
    )

    assert result["metrics"] == []
    assert result["metrics_total"] == 1
    recovery = result["recovery"]["metrics"]
    assert recovery["remaining"] == 1
    assert "only=['metrics']" in recovery["call"]
    assert "cursor=0" in recovery["call"]
    assert "limit=1" in recovery["call"]


@pytest.mark.asyncio
async def test_get_health_dashboard_surfaces_maintainability(setup_mcp, health_data):
    """The maintainability pillar is surfaced as a co-equal second signal."""
    from repowise.server.mcp_server import get_health

    result = await get_health(only=["kpis", "worst_files", "top_findings"])
    # Repo-level KPI headline for the maintainability pillar.
    # NLOC-weighted: (6.0*200 + 9.0*50) / 250 = 6.6.
    assert result["kpis"]["maintainability_average"] == 6.6
    # Per-file metrics carry all three dimension scores — the defect one under
    # its surfaced name ``score`` (there is no duplicate ``defect_score``).
    worst = result["worst_files"][0]
    assert worst["score"] == 4.5
    assert worst["maintainability_score"] == 6.0
    assert worst["performance_score"] == 9.0
    # Findings are tagged with their home pillar so they can be filtered.
    # Performance is not in the impact ranking: it has no impact to rank by.
    dims = {f["dimension"] for f in result["top_findings"]}
    assert dims == {"defect", "maintainability"}


@pytest.mark.asyncio
async def test_get_health_dashboard_surfaces_performance(setup_mcp, health_data):
    """The performance pillar is surfaced as a co-equal third signal."""
    from repowise.server.mcp_server import get_health

    result = await get_health(only=["kpis", "top_findings"])
    # Repo-level KPI headline for the performance pillar.
    # NLOC-weighted: (9.0*200 + 10.0*50) / 250 = 9.2.
    assert result["kpis"]["performance_average"] == 9.2
    # The perf finding carries its boundary kind + cross-function reachability
    # path. Asked for by name, because the impact ranking above excludes it.
    ranked = await get_health(include=["performance"], only=["top_findings"])
    perf = [f for f in ranked["top_findings"] if f["dimension"] == "performance"]
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

    result = await get_health(
        only=["kpis", "gap_analysis", "worst_files", "high_leverage_files"]
    )
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
    """refactoring_plans is bounded by limit and reports the honest total.

    Named explicitly: ``include=["refactoring"]`` leads with the composed
    opportunity queue, and the raw plan list is the opt-in projection.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health(
        include=["refactoring"], only=["refactoring_plans"], limit=5
    )
    assert "refactoring_plans" in result
    assert len(result["refactoring_plans"]) <= 5
    # Honest truncation signal is always present when refactoring is requested.
    assert "refactoring_plans_total" in result


async def _seed_plans(session, rid, plans):
    """Store refactoring suggestions for the directive / ranking tests."""
    from repowise.core.persistence import crud

    await crud.save_refactoring_suggestions(
        session,
        rid,
        [
            {
                "refactoring_type": p.get("refactoring_type", "extract_method"),
                "file_path": p["file_path"],
                "target_symbol": p.get("target_symbol", "authenticate"),
                "line_start": 10,
                "line_end": 80,
                "plan": {"groups": []},
                "evidence": {},
                "impact_delta": p["impact_delta"],
                "effort_bucket": "S",
                "blast_radius": {"dependents_count": 0},
                "confidence": "high",
                "source_biomarker": p["source_biomarker"],
            }
            for p in plans
        ],
    )
    await session.commit()


@pytest.mark.asyncio
async def test_entity_recovery_retains_health_semantics_and_freshness(
    setup_mcp, health_data, session
):
    from repowise.server.mcp_server import get_health

    finding = (await get_health(only=["top_findings"], limit=1))["top_findings"][0]
    finding_detail = await get_health(finding_id=finding["id"])
    assert finding_detail["finding"] == finding
    assert finding_detail["_meta"]["health_semantics"]
    assert finding_detail["_meta"]["health_analysis"]["recomputed_this_call"] is False

    await _seed_plans(
        session,
        health_data,
        [
            {
                "file_path": "src/auth/service.py",
                "impact_delta": 1.2,
                "source_biomarker": "complex_method",
            }
        ],
    )
    plan = (
        await get_health(
            targets=["src/auth/service.py"],
            include=["refactoring"],
            only=["refactoring_plans"],
            limit=1,
        )
    )["refactoring_plans"][0]
    plan_detail = await get_health(plan_id=plan["id"])
    assert plan_detail["plan"]["id"] == plan["id"]
    assert plan_detail["plan"]["file_path"] == plan["file_path"]
    assert plan_detail["_meta"]["health_semantics"]
    assert plan_detail["_meta"]["health_analysis"]["recomputed_this_call"] is False


def test_validation_profiles_deduplicate_without_dropping_commands_or_target_tests():
    from repowise.server.mcp_server.tool_health import _validation_profile

    validation = {
        "tests": ["tests/test_service.py::test_auth"],
        "total": 1,
        "commands": ["pytest tests/test_service.py::test_auth"],
        "targets": [
            {
                "file_path": "src/auth/service.py",
                "tests": ["tests/test_service.py::test_auth"],
                "total": 1,
            }
        ],
    }
    _profile_id, profile = _validation_profile(validation)

    assert profile["commands"] == validation["commands"]
    assert profile["commands_total"] == profile["commands_emitted"] == 1
    assert profile["targets"][0]["tests"] == validation["targets"][0]["tests"]


@pytest.mark.asyncio
async def test_freshness_is_a_repository_fact_in_every_mode(setup_mcp, health_data, session):
    """Whether the analysis recorded its commit cannot depend on the mode asked.

    Each mode used to compute "latest analysed row" over whatever rows it was
    reporting on, so a call scoped to an older file reported that file's commit
    as the repository's, and a detail call could read ``available`` at the same
    instant the dashboard read ``degraded``. The block is repository-wide now.
    """
    from datetime import UTC, datetime

    from sqlalchemy import select

    from repowise.core.persistence.models import HealthFileMetric
    from repowise.server.mcp_server import get_health

    rows = list(
        (
            await session.execute(
                select(HealthFileMetric).where(HealthFileMetric.repository_id == health_data)
            )
        )
        .scalars()
        .all()
    )
    by_path = {row.file_path: row for row in rows}
    by_path["src/auth/service.py"].updated_at = datetime(2025, 1, 1, tzinfo=UTC)
    by_path["src/auth/service.py"].analyzed_commit = "a" * 40
    by_path["src/db/models.py"].updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    by_path["src/db/models.py"].analyzed_commit = "b" * 40
    await session.flush()

    scoped = await get_health(targets=["src/auth/service.py"], only=["metrics"])
    dashboard = await get_health(only=["kpis"])
    for meta in (scoped["_meta"], dashboard["_meta"]):
        # The repository's newest analysed row, not the scoped file's.
        assert meta["health_analyzed_at"].startswith("2026-01-01")
        assert meta["health_analyzed_commit"] == "b" * 12
        assert meta["health_analyzed_commits_distinct"] == 2
    assert (
        scoped["_meta"]["health_analysis"]["status"]
        == dashboard["_meta"]["health_analysis"]["status"]
    )


@pytest.mark.asyncio
async def test_directive_admits_when_the_file_has_no_plans_at_all(setup_mcp, health_data):
    """``plan_via`` promised a fix for ``reason``; with no plans it cannot deliver one."""
    from repowise.server.mcp_server import get_health

    result = await get_health(only=["directive"])
    directive = result["directive"]
    # The seeded worst file leads with complex_method and carries no plans.
    assert directive["fix_first"] == "src/auth/service.py"
    assert directive["plan_addresses_reason"] is False
    assert "has no plans" in directive["plan_note"]
    assert directive["next_action"] == "investigate complex_method"


@pytest.mark.asyncio
async def test_directive_admits_when_plans_target_a_different_biomarker(
    setup_mcp, health_data, session
):
    """The failure this ships for: plans exist, for a cause other than the one named."""
    from repowise.server.mcp_server import get_health

    await _seed_plans(
        session,
        health_data,
        [
            {
                "file_path": "src/auth/service.py",
                "source_biomarker": "dry_violation",
                "impact_delta": 1.0,
            }
        ],
    )

    directive = (await get_health(only=["directive"]))["directive"]
    assert directive["plan_addresses_reason"] is False
    # Names the gap on both sides: the unaddressed cause and what is on offer.
    assert "complex_method" in directive["plan_note"]
    assert "dry_violation" in directive["plan_note"]


@pytest.mark.asyncio
async def test_directive_confirms_when_a_plan_addresses_the_reason(setup_mcp, health_data, session):
    """The true branch has to be reachable, or the flag is decoration."""
    from repowise.server.mcp_server import get_health

    await _seed_plans(
        session,
        health_data,
        [
            {
                "file_path": "src/auth/service.py",
                "source_biomarker": "complex_method",
                "impact_delta": 1.0,
            }
        ],
    )

    directive = (await get_health(only=["directive"]))["directive"]
    assert directive["plan_addresses_reason"] is True
    assert "plan_note" not in directive
    assert directive["next_action"] == "inspect matching plan via plan_via"


@pytest.mark.asyncio
async def test_combined_recommendation_lede_is_compact_and_self_directing(
    setup_mcp, health_data, session
):
    from repowise.server.mcp_server import get_health

    await _seed_plans(
        session,
        health_data,
        [
            {
                "file_path": "src/auth/service.py",
                "source_biomarker": "complex_method",
                "impact_delta": 1.0,
            }
        ],
    )
    # The queue is materialized, so the fixture's findings only reach the lede
    # once the writer both index paths use has grouped them.
    from repowise.core.persistence.crud import finalize_performance_opportunities

    await finalize_performance_opportunities(session, health_data)
    await session.commit()

    result = await get_health(include=["performance", "refactoring"], only=["recommendation_lede"])
    lede = result["recommendation_lede"]
    assert set(result) <= {"mode", "recommendation_lede", "_meta"}
    assert lede["performance_opportunities_total"] == 1
    # Two: the seeded extract-class plan, and the authoritative performance
    # plan the finalizer generated for the fixture's own opportunity.
    assert lede["refactoring_plans_total"] == 2
    assert lede["performance_lead"]["boundary_kind"] == "db"
    assert {"benefit", "leverage", "cost", "risk"} <= set(lede["recommendation_lead"])
    assert "validation" not in lede["recommendation_lead"]
    assert "only=['performance_opportunities','refactoring_plans']" in lede["next_call"]


@pytest.mark.asyncio
async def test_directive_does_not_claim_a_file_is_planless_over_an_unattributed_plan(
    setup_mcp, health_data, session
):
    """``split_file`` and ``break_cycle`` store an empty ``source_biomarker``.

    Keying "has plans" off the biomarker set would tell the caller this file has
    no plans while the highest-leverage plan kind sits on it — a false statement
    in the one field that exists to stop the tool over-promising.
    """
    from repowise.server.mcp_server import get_health

    await _seed_plans(
        session,
        health_data,
        [
            {
                "file_path": "src/auth/service.py",
                "refactoring_type": "split_file",
                "source_biomarker": "",
                "impact_delta": 2.0,
            }
        ],
    )

    directive = (await get_health(only=["directive"]))["directive"]
    assert directive["plan_addresses_reason"] is False
    assert "has no plans" not in directive["plan_note"]
    assert "record no source biomarker" in directive["plan_note"]


@pytest.mark.asyncio
async def test_directive_stays_silent_when_the_file_has_no_named_cause(
    setup_mcp, health_data, session
):
    """No lead biomarker means nothing to report a plan gap *about*.

    ``reason`` already degrades to the bare score here, so a note would read
    "No stored plan addresses None".
    """
    from repowise.core.persistence.crud import save_health_metrics
    from repowise.server.mcp_server import get_health

    # Big and low-scoring, so it outranks the seeded worst file on leverage —
    # but with no findings at all, so it has no lead.
    await save_health_metrics(
        session,
        health_data,
        [
            {
                "file_path": "src/legacy/blob.py",
                "score": 2.0,
                "max_ccn": 1,
                "max_nesting": 1,
                "nloc": 5000,
                "has_test_file": False,
                "module": "legacy",
            }
        ],
    )
    await session.commit()

    directive = (await get_health(only=["directive"]))["directive"]
    assert directive["fix_first"] == "src/legacy/blob.py"
    assert directive["plan_addresses_reason"] is False
    assert "plan_note" not in directive
    assert "None" not in directive["reason"]


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
    assert "findings" not in result
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
    assert result["findings_total"] == 3

    dash = await get_health(only=["top_findings"], limit=2)
    assert len(dash["top_findings"]) == 2
    assert dash["top_findings_total"] == 3


@pytest.mark.asyncio
async def test_get_health_modules_capped_with_honest_total(setup_mcp, health_data):
    from repowise.server.mcp_server import get_health

    result = await get_health(only=["modules"], limit=1)
    assert len(result["modules"]) == 1
    assert result["modules_total"] == 2


@pytest.mark.asyncio
async def test_get_health_suggestion_text_emitted_once_as_legend(setup_mcp, health_data):
    """Suggestion prose is keyed by biomarker type, so it ships once, not per row."""
    from repowise.server.mcp_server import get_health

    result = await get_health(
        include=["refactoring"], only=["top_findings", "suggestion_legend"]
    )
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
    assert d["recovers_weighted_deficit_points"] == d["recovers_points"]
    assert d["recovers_points_compatibility"] == {
        "deprecated": True,
        "replacement": "recovers_weighted_deficit_points",
        "equivalent_value": True,
    }
    # The only below-target file holds the whole gross deficit (700/700), so
    # the share is 100% by construction — the net gap (675) is not the
    # denominator, since healthy files would cushion it (issue #1437).
    assert d["share_of_repo_gap_pct"] == pytest.approx(100.0)
    assert d["then"] == []  # only one below-target file in the fixture


@pytest.mark.asyncio
async def test_directive_share_bounded_when_gross_exceeds_net_gap(session, setup_mcp):
    """Regression guard for #1437: the share uses the gross deficit of all
    below-target files as its denominator, so it is bounded by 100% and sums to
    100% by construction — the net gap (which healthy files cushion) would let
    a single file read as closing more than the whole remaining gap."""
    from repowise.core.persistence.crud import save_health_metrics
    from repowise.server.mcp_server import get_health

    rid = setup_mcp
    await save_health_metrics(
        session,
        rid,
        [
            # One big low file: gross deficit (8.0 - 3.0) * 400 = 2000.
            {
                "file_path": "src/legacy/blob.py",
                "score": 3.0,
                "max_ccn": 20,
                "max_nesting": 6,
                "nloc": 400,
                "has_test_file": False,
                "module": "legacy",
                "defect_score": 3.0,
                "maintainability_score": 4.0,
                "performance_score": 8.0,
            },
            # Many small healthy files: surplus 8.5-8.0 = 0.5 * 100 each.
            *[
                {
                    "file_path": f"src/ok/module{i}.py",
                    "score": 8.5,
                    "max_ccn": 3,
                    "max_nesting": 1,
                    "nloc": 100,
                    "has_test_file": True,
                    "module": "ok",
                    "defect_score": 8.5,
                    "maintainability_score": 9.0,
                    "performance_score": 10.0,
                }
                for i in range(4)
            ],
        ],
    )

    result = await get_health()
    d = result["directive"]
    # Net gap = 8.0*800 - (3.0*400 + 8.5*400) = 6400 - 4600 = 1800, and the
    # single below-target file owns the whole gross deficit: 2000/2000 = 100%.
    assert d["recovers_points"] == 2000
    assert d["share_of_repo_gap_pct"] == pytest.approx(100.0)
    # The gross gap is the reported denominator.
    assert result["gap_analysis"]["weighted_gross_gap_points"] == 2000
    assert result["gap_analysis"]["weighted_gap_points"] == 1800


@pytest.mark.asyncio
async def test_directive_absent_when_no_file_below_target(session, setup_mcp):
    """When no file is below the Healthy floor there is no gross gap, nothing to
    recommend, and no share to report — the directive is absent entirely."""
    from repowise.core.persistence.crud import save_health_metrics
    from repowise.server.mcp_server import get_health

    rid = setup_mcp
    await save_health_metrics(
        session,
        rid,
        [
            {
                "file_path": "src/ok/module.py",
                "score": 8.5,
                "max_ccn": 3,
                "max_nesting": 1,
                "nloc": 100,
                "has_test_file": True,
                "module": "ok",
                "defect_score": 8.5,
                "maintainability_score": 9.0,
                "performance_score": 10.0,
            }
        ],
    )

    result = await get_health()
    # No file is below the Healthy floor, so there is nothing to recommend and
    # no gross gap to share — the directive is absent entirely.
    assert result["directive"] is None
    assert result["gap_analysis"]["weighted_gross_gap_points"] == 0


@pytest.mark.asyncio
async def test_high_leverage_rows_sum_to_100_with_negative_net_gap(session, setup_mcp):
    """The microdot scenario from #1437: several files below target against a
    negative net gap. The gross gap is the denominator, so rows are distinct,
    each bounded by 100%, and they sum to 100% by construction — whereas the
    net gap (0, since the average is above 8.0) would report nothing at all."""
    from repowise.core.persistence.crud import save_health_metrics
    from repowise.server.mcp_server import get_health

    rid = setup_mcp
    await save_health_metrics(
        session,
        rid,
        [
            # Three below-target files of different sizes.
            {
                "file_path": "src/a/big.py",
                "score": 6.0,
                "max_ccn": 10,
                "max_nesting": 4,
                "nloc": 400,
                "has_test_file": False,
                "module": "a",
                "defect_score": 6.0,
                "maintainability_score": 7.0,
                "performance_score": 9.0,
            },
            {
                "file_path": "src/b/mid.py",
                "score": 7.0,
                "max_ccn": 6,
                "max_nesting": 3,
                "nloc": 200,
                "has_test_file": False,
                "module": "b",
                "defect_score": 7.0,
                "maintainability_score": 8.0,
                "performance_score": 9.5,
            },
            {
                "file_path": "src/c/small.py",
                "score": 7.5,
                "max_ccn": 4,
                "max_nesting": 2,
                "nloc": 100,
                "has_test_file": True,
                "module": "c",
                "defect_score": 7.5,
                "maintainability_score": 8.5,
                "performance_score": 10.0,
            },
            # One healthy file, large enough that the weighted average is
            # already above 8.0 (negative net gap — the microdot shape).
            {
                "file_path": "src/d/ok.py",
                "score": 9.0,
                "max_ccn": 2,
                "max_nesting": 1,
                "nloc": 1200,
                "has_test_file": True,
                "module": "d",
                "defect_score": 9.0,
                "maintainability_score": 9.5,
                "performance_score": 10.0,
            },
        ],
    )

    result = await get_health()
    gap = result["gap_analysis"]
    rows = result["high_leverage_files"]
    assert len(rows) == 3

    # Gross deficits: (8-6)*400=800, (8-7)*200=200, (8-7.5)*100=50 → 1050.
    assert gap["weighted_gross_gap_points"] == 1050
    # Net gap is negative: 8*1900 - (6*400+7*200+7.5*100+9*1200) = 15200 - 15350.
    assert gap["weighted_gap_points"] == 0
    # Rows are distinct (ranking survives) and bounded, summing to 100%.
    shares = {r["file_path"]: r["share_of_repo_gap_pct"] for r in rows}
    assert shares == pytest.approx(
        {
            "src/a/big.py": round(100.0 * 800 / 1050, 1),
            "src/b/mid.py": round(100.0 * 200 / 1050, 1),
            "src/c/small.py": round(100.0 * 50 / 1050, 1),
        }
    )
    assert len({v for v in shares.values()}) == 3  # distinct, not collapsed
    assert sum(shares.values()) == pytest.approx(100.0, abs=0.1)
    # The directive quotes the same lead-file share.
    assert result["directive"]["share_of_repo_gap_pct"] == pytest.approx(
        shares["src/a/big.py"], abs=0.1
    )


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
    assert result["unknown_only_keys_total"] == 1
    assert result["unknown_only_keys_emitted"] == 1
    assert "directive" in result


@pytest.mark.asyncio
async def test_get_health_include_names_unknown_keys(setup_mcp, health_data):
    from repowise.server.mcp_server import get_health

    result = await get_health(include=["trend", "typo"])
    assert result["unknown_include_keys"] == ["typo"]
    assert result["unknown_include_keys_total"] == 1
    assert result["unknown_include_keys_emitted"] == 1


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
    dash = await get_health(only=["worst_files"])
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
    assert result["findings_total"] == 3
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

    capped = await get_health(only=["top_findings"], limit=1)
    assert len(capped["top_findings"]) == 1
    assert capped["top_findings_total"] == 3

    scoped = await get_health(targets=["src/auth/service.py"], limit=1)
    assert len(scoped["findings"]) == 1
    assert scoped["findings_total"] == 3


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

    full = await get_health(only=["kpis", "worst_files"])
    filtered = await get_health(
        include=["biomarkers", "maintainability"], only=["kpis", "worst_files"]
    )
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

    result = await get_health(only=["worst_files"])
    assert [m["file_path"] for m in result["worst_files"]] == [
        "src/z.py",
        "src/c.py",
        "src/b.py",
        "src/a.py",
    ]

    # The cap is where the old order actually hurt: under a score-only sort the
    # worst file falls off the page entirely.
    capped = await get_health(only=["worst_files"], limit=1)
    assert [m["file_path"] for m in capped["worst_files"]] == ["src/z.py"]


@pytest.mark.asyncio
async def test_worst_files_order_survives_every_dimension_filter(setup_mcp, floored_health_data):
    """Ranking reads the unfiltered open set, whatever the caller asked to see.

    The combination is the point: a cap and a filter interacting is where this
    tool has broken twice. Narrowing the *findings* to one dimension must not
    silently re-rank which files the repo's worst are.
    """
    from repowise.server.mcp_server import get_health

    baseline = [
        m["file_path"]
        for m in (await get_health(only=["worst_files"], limit=2))["worst_files"]
    ]
    assert baseline == ["src/z.py", "src/c.py"]

    for include in (["defect"], ["maintainability"], ["performance"], ["biomarkers", "defect"]):
        result = await get_health(include=include, only=["worst_files"], limit=2)
        assert [m["file_path"] for m in result["worst_files"]] == baseline, include


@pytest.mark.asyncio
async def test_one_response_agrees_with_itself_about_the_worst_file(setup_mcp, floored_health_data):
    """``kpis`` and ``worst_files`` must name the same file.

    Regression: ``_compute_kpis`` reduces with ``min()``, which returns the
    *first* minimum, so it answered from whatever order it was handed. Ranking
    ``worst_files`` without also ranking the list behind the KPIs produced a
    payload whose headline named one file as the worst performer while the
    list printed directly beneath it led with another.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health(only=["kpis", "worst_files", "modules"])
    assert result["kpis"]["worst_performer_path"] == result["worst_files"][0]["file_path"]
    assert result["kpis"]["worst_performer_path"] == "src/z.py"

    # Same reduction, same tie, one level down: the module rollup's worst
    # performer is picked with ``min()`` over the same rows.
    assert [m["worst_performer_path"] for m in result["modules"]] == ["src/z.py"]


# ---------------------------------------------------------------------------
# Projection integrity: ``only`` x ``include`` x mode.
#
# Three separate dogfooding passes have now found bugs in the *interaction* of
# these parameters and nowhere else, so these enumerate combinations rather
# than inspect one response.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "include"),
    [
        ("worst_files", None),
        ("high_leverage_files", None),
        ("top_findings", None),
        ("test_findings", None),
        ("modules", None),
        ("findings", ["biomarkers"]),
        ("refactoring_plans", ["refactoring"]),
    ],
)
async def test_only_retains_the_total_for_every_capped_list(setup_mcp, health_data, key, include):
    """``only=[list]`` keeps that list's ``*_total`` sibling.

    Regression: the projection kept exactly the named keys, so the tool's own
    "each carries a ``*_total`` sibling so truncation is never silent" promise
    broke precisely when a caller economized. ``only=["modules"]`` at
    ``limit=50`` returned 50 of 116 modules with nothing saying so.

    Asking the caller to name the total themselves is not a fix: a caller who
    knew to ask for it would not need the guarantee.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health(include=include, only=[key], limit=1)
    assert key in result, result.get("unknown_only_keys")
    assert f"{key}_total" in result, f"{key} lost its total under a projection"
    # And it is the real count, not the length of the capped list.
    full = await get_health(include=include, only=[key], limit=1)
    assert result[f"{key}_total"] == full[f"{key}_total"]


@pytest.mark.asyncio
async def test_worst_files_and_high_leverage_files_report_totals(setup_mcp, health_data):
    """Both ranked file lists carry a total, like every other capped list.

    ``worst_files_total`` did not exist in any projection — asking for it came
    back in ``unknown_only_keys``, which reads as "you misspelled it" for a key
    that was simply never emitted.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health(only=["worst_files", "high_leverage_files"], limit=1)
    assert len(result["worst_files"]) == 1
    assert result["worst_files_total"] == 2
    assert len(result["high_leverage_files"]) == 1
    # Leverage only ranks the below-Healthy files, so this is not file_count.
    assert result["high_leverage_files_total"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("alias", "resolved", "include"),
    [
        ("biomarkers", "findings", ["biomarkers"]),
        ("accuracy", "defect_accuracy", ["accuracy"]),
        ("refactoring", "refactoring_plans", ["refactoring"]),
    ],
)
async def test_include_names_work_as_only_aliases(setup_mcp, health_data, alias, resolved, include):
    """``include`` and ``only`` were two vocabularies for the same blocks.

    A caller switches a block on with ``include=["biomarkers"]`` and it lands
    under the key ``findings``, so the obvious ``only=["biomarkers"]`` projected
    away the very block just asked for — reported only via
    ``unknown_only_keys``, which is what kept it survivable rather than silent.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health(include=include, only=[alias])
    assert resolved in result
    # An alias that resolves is not "unknown".
    assert "unknown_only_keys" not in result
    allowed = {
        resolved,
        f"{resolved}_total",
        f"{resolved}_emitted",
        f"{resolved}_reduced_reason",
    }
    if resolved == "refactoring_plans":
        allowed |= {
            "refactoring_plans_status",
            "validation_profiles",
            "validation_profiles_total",
            "validation_profiles_emitted",
            "validation_profiles_reduced_reason",
        }
    assert set(result) - {"mode", "targets", "_meta"} <= allowed


@pytest.mark.asyncio
async def test_only_signals_is_reported_rather_than_answered_empty(setup_mcp, health_data):
    """``signals`` has no top-level key, and saying so is the whole fix.

    It merges into ``metrics[].signals``, so ``only=["signals"]`` can only ever
    return an empty response. Deliberately *not* aliased — there is no key to
    alias it to — so it must keep landing in ``unknown_only_keys``.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health(include=["signals"], only=["signals"])
    assert result["unknown_only_keys"] == ["signals"]


@pytest.mark.asyncio
async def test_suggestion_legend_survives_a_projection(setup_mcp, health_data_with_tests):
    """A projection subtracts keys; it must not change what a kept key holds.

    Regression: the legend was built from ``result["findings"]`` /
    ``["top_findings"]``, which the projection's work-gating can skip building.
    So ``only=["refactoring_plans", "suggestion_legend"]`` returned
    ``suggestion_legend: {}``, and adding ``top_findings`` back to ``only``
    refilled it — a performance optimization that had silently become a
    content change.

    Deliberately on the fixture that *has* test material. The legend derives
    from the production and test heads, so on a fixture with no test files
    every projection agrees for the wrong reason and this test cannot fail —
    which is exactly what happened when the ``is_test`` read was later gated
    for performance and silently dropped ``suggestion_legend`` from the gate.
    """
    from repowise.server.mcp_server import get_health

    full = await get_health(
        include=["refactoring"],
        only=["refactoring_plans", "suggestion_legend", "top_findings", "test_findings"],
    )
    assert full["suggestion_legend"], "fixture should produce legend entries"

    without_findings = await get_health(
        include=["refactoring"], only=["refactoring_plans", "suggestion_legend"]
    )
    with_findings = await get_health(
        include=["refactoring"], only=["refactoring_plans", "suggestion_legend", "top_findings"]
    )
    assert without_findings["suggestion_legend"] == full["suggestion_legend"]
    assert with_findings["suggestion_legend"] == full["suggestion_legend"]

    # And it explains every biomarker the response actually shows.
    shown = {f["biomarker_type"] for f in full["top_findings"] + full["test_findings"]}
    assert shown <= set(full["suggestion_legend"])


@pytest.mark.asyncio
async def test_limit_zero_means_no_rows_not_one_row(setup_mcp, health_data):
    """``0`` means none, matching the ``module_limit`` convention.

    It used to clamp up to 1, so the documented way to ask for "the totals,
    none of the rows" quietly returned a row, and a caller trimming a payload
    could not tell the clamp had happened.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health(limit=0)
    assert result["high_leverage_files"] == []
    assert result["high_leverage_files_total"] == 1
    for key in ("worst_files", "top_findings", "test_findings", "modules"):
        projected = await get_health(only=[key], limit=0)
        assert projected[key] == [], key
        assert projected[f"{key}_emitted"] == 0
        assert projected[f"{key}_total"] >= 0


@pytest.mark.asyncio
async def test_the_directive_is_identical_at_every_limit(setup_mcp, health_data):
    """``limit`` caps ranked lists. The directive is not one, so it must not move.

    Regression, and it was introduced by making ``limit=0`` reachable: the
    per-file lead reduction was scoped to ``metric_rows[:limit] |
    by_leverage[:limit]``, so at ``limit=0`` the directive's own candidates had
    no lead. ``fix_first`` survived (it reads ``by_leverage[0]``, not the
    leads), which is exactly why asserting ``fix_first`` alone was not enough —
    ``reason`` fell back to "scores N", ``plan_note`` vanished, and
    ``plan_addresses_reason`` became an unconditional ``False``: a wrong claim
    rather than a missing one.
    """
    from repowise.server.mcp_server import get_health

    baseline = (await get_health())["directive"]
    assert baseline["reason"]
    for limit in (0, 1, 2, 50):
        directive = (await get_health(limit=limit))["directive"]
        assert directive == baseline, f"directive moved at limit={limit}"
    # And through the projection that makes it the cheapest call.
    assert (await get_health(only=["directive"], limit=0))["directive"] == baseline


# ---------------------------------------------------------------------------
# Test material in the defect dashboard
# ---------------------------------------------------------------------------


@pytest.fixture
async def health_data_with_tests(session, health_data: str) -> str:
    """Add a test file that would dominate the headline finding list.

    ``tests/test_service.py`` is already seeded in ``graph_nodes`` with
    ``is_test=True``; this gives it health rows. Its findings carry the two
    highest impacts in the fixture, so an unsplit list leads with the test
    suite — which is the shape measured on the real index, where 2 of the top
    5 open findings repo-wide sat on test files.

    Rows are added through the ORM rather than ``save_health_metrics`` /
    ``save_health_findings``: both are delete-then-insert over the whole repo,
    so calling them here would wipe the base fixture instead of extending it.
    """
    import json
    import uuid

    from repowise.core.persistence.models import HealthFileMetric, HealthFinding

    rid = health_data
    session.add(
        HealthFileMetric(
            id=str(uuid.uuid4()),
            repository_id=rid,
            file_path="tests/test_service.py",
            score=3.0,
            max_ccn=12,
            max_nesting=4,
            nloc=120,
            has_test_file=False,
            module="tests",
        )
    )
    for biomarker, fn, impact, reason in (
        ("change_entropy", None, 3.02, "tests/test_service.py changes with unrelated files"),
        ("complex_method", "test_everything", 2.5, "test_everything has complexity 12"),
    ):
        session.add(
            HealthFinding(
                id=str(uuid.uuid4()),
                repository_id=rid,
                file_path="tests/test_service.py",
                biomarker_type=biomarker,
                severity="critical" if fn is None else "high",
                function_name=fn,
                line_start=None if fn is None else 1,
                line_end=None if fn is None else 90,
                details_json=json.dumps({}),
                health_impact=impact,
                reason=reason,
                dimension="defect",
                status="open",
            )
        )
    await session.flush()
    return rid


@pytest.mark.asyncio
async def test_test_findings_get_their_own_bucket(setup_mcp, health_data_with_tests):
    """Test material stops competing for the repo's headline finding list.

    Measured on this repo before the split: 5 of the top 20 open findings by
    impact sat on test files, 2 of the top 5. Defect risk in a test asks a
    different question from defect risk in the code it covers, and a
    high-churn test file usually means active development rather than
    fragility — so it is bucketed, not dropped.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health(only=["top_findings", "test_findings"])

    # Unsplit, the two highest-impact findings in the fixture are both tests.
    assert [f["file_path"] for f in result["test_findings"]] == [
        "tests/test_service.py",
        "tests/test_service.py",
    ]
    assert all(f["file_path"] != "tests/test_service.py" for f in result["top_findings"])
    assert result["top_findings"][0]["file_path"] == "src/auth/service.py"

    # Nothing that ranks is lost: the two buckets partition the impact-ranked
    # set, which is defect and maintainability work. The fixture's performance
    # finding is in neither, because it carries no impact to rank by.
    assert result["test_findings_total"] == 2
    assert result["top_findings_total"] == 3
    assert result["top_findings_total"] + result["test_findings_total"] == 5


@pytest.mark.asyncio
async def test_each_bucket_is_capped_against_its_own_population(setup_mcp, health_data_with_tests):
    """The split happens before the cap, not after.

    Capping first and partitioning after would hand the smaller bucket
    whatever happened to land in the shared head — so with two test findings
    above every production one, ``top_findings`` would come back empty at
    ``limit=2`` while ``top_findings_total`` claimed four.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health(only=["top_findings", "test_findings"], limit=2)
    assert len(result["top_findings"]) == 2
    assert len(result["test_findings"]) == 2
    assert result["top_findings_total"] == 3


@pytest.mark.asyncio
async def test_metric_rows_say_whether_a_file_is_test_material(setup_mcp, health_data_with_tests):
    """``is_test`` is a fact on the row, distinct from ``has_test_file``.

    The two answer opposite questions — "is this file a test" vs "is this file
    tested" — and nothing in the payload used to say which one you were
    looking at.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health(only=["worst_files", "high_leverage_files"])
    by_path = {m["file_path"]: m for m in result["worst_files"]}
    assert by_path["tests/test_service.py"]["is_test"] is True
    assert by_path["src/auth/service.py"]["is_test"] is False
    # Both ranked file lists carry it.
    assert all("is_test" in m for m in result["high_leverage_files"])


@pytest.mark.asyncio
async def test_targeted_mode_is_never_split(setup_mcp, health_data_with_tests):
    """Naming a test file must return its findings, not bucket them away.

    The split answers "where is the defect risk in this codebase". A caller
    who named the file already answered that question themselves.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["tests/test_service.py"])
    assert result["mode"] == "targets"
    assert "test_findings" not in result
    assert len(result["findings"]) == 2
    assert result["findings_total"] == 2
    assert result["metrics"][0]["is_test"] is True


@pytest.mark.asyncio
async def test_targeted_mode_asks_only_about_the_files_it_was_given(
    setup_mcp, health_data_with_tests, monkeypatch
):
    """The test-path read is scoped to the targets in targeted mode.

    It answers one question per mode. Targeted mode only ever asks
    ``path in test_paths`` for paths the caller named, so it reads exactly
    those — a keyed seek instead of a read over every file node the repo has.
    Measured on the repowise index, 32.9ms -> 0.6ms on a single-file target,
    which was a quarter of the whole call spent deciding whether one file is a
    test. Dashboard mode partitions a ranked finding list whose paths are not
    known until that list is built, so it must stay repo-wide.
    """
    import repowise.server.mcp_server.tool_health as th
    from repowise.server.mcp_server import get_health

    asked: list[object] = []
    real = th.get_test_file_paths

    async def spy(session, repository_id, paths=None):
        asked.append(paths)
        return await real(session, repository_id, paths)

    monkeypatch.setattr(th, "get_test_file_paths", spy)

    targeted = await get_health(targets=["tests/test_service.py"])
    assert asked == [["tests/test_service.py"]]
    # Scoping it must not change the answer.
    assert targeted["metrics"][0]["is_test"] is True

    asked.clear()
    dashboard = await get_health(only=["test_findings"])
    assert asked == [None], "the dashboard split needs the repo-wide answer"
    assert dashboard["test_findings"], "scoping the dashboard would empty this"


@pytest.mark.asyncio
async def test_kpis_still_include_test_files(setup_mcp, health_data_with_tests):
    """Excluding test material from the KPIs is a scoring change, not a display one.

    Measured across this workspace, dropping tests moves NLOC-weighted
    ``average_health`` 7.52 -> 6.87 on this repo, 7.07 -> 6.27 on the backend
    and 7.59 -> 7.46 on the frontend: test files score *better* than
    production code, so excluding them would drop every repo's headline
    overnight with no defect having been found.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health(only=["kpis", "worst_files"])
    assert result["kpis"]["file_count"] == 3
    assert any(m["file_path"] == "tests/test_service.py" for m in result["worst_files"])


@pytest.mark.asyncio
async def test_the_split_survives_a_dimension_filter(setup_mcp, health_data_with_tests):
    """A cap, a filter and a partition over one list — the shape that keeps breaking.

    Both totals must describe the *filtered* set, and still sum to it.
    """
    from repowise.server.mcp_server import get_health

    unfiltered = await get_health(only=["top_findings", "test_findings"])
    filtered = await get_health(
        include=["biomarkers", "defect"],
        only=["findings", "test_findings"],
    )
    assert all(f["dimension"] == "defect" for f in filtered["findings"])
    assert all(f["dimension"] == "defect" for f in filtered["test_findings"])
    assert (
        filtered["findings_total"] + filtered["test_findings_total"]
        < unfiltered["top_findings_total"] + unfiltered["test_findings_total"]
    )


# ---------------------------------------------------------------------------
# Coverage: the read, not just the response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_coverage_declines_the_covered_lines_column(setup_mcp, health_data):
    """The dashboard asks the data layer not to read the blob it never emits.

    This asserts the *read*, deliberately. The response was already correct —
    the dashboard built each row wide, ``json.loads``-ing every
    ``covered_lines_json``, and then stripped ``covered_lines`` back out with a
    dict comprehension. So a test that only checked the payload would pass on
    the unfixed code; the waste is invisible from the outside.
    """
    from repowise.server.mcp_server import get_health, tool_health

    seen: list[bool] = []
    real = tool_health.load_coverage_for_repo

    async def spy(*args, **kwargs):
        seen.append(kwargs.get("include_covered_lines", True))
        return await real(*args, **kwargs)

    tool_health.load_coverage_for_repo = spy
    try:
        await get_health(include=["coverage"])
        assert seen == [False], "dashboard mode still read the covered-line arrays"
        seen.clear()
        await get_health(include=["coverage"], targets=["src/auth/service.py"])
        assert seen == [True], "targeted mode serializes covered_lines and must read it"
    finally:
        tool_health.load_coverage_for_repo = real


@pytest.mark.asyncio
async def test_coverage_payload_shape_is_unchanged(setup_mcp, health_data):
    """Guards the restructure, not a past bug — it passes on the reverted code.

    Kept because the narrow rows are a different object: they carry no
    ``covered_lines_json`` at all, so the old build-wide-then-subtract would
    now raise rather than merely waste the parse.
    """
    from repowise.server.mcp_server import get_health

    dashboard = await get_health(include=["coverage"])
    for row in dashboard["coverage"]["files"]:
        assert "covered_lines" not in row
        assert {"file_path", "line_coverage_pct", "total_coverable_lines"} <= set(row)

    targeted = await get_health(include=["coverage"], targets=["src/auth/service.py"])
    for row in targeted["coverage"]["files"]:
        assert "covered_lines" in row


@pytest.mark.asyncio
async def test_refactoring_plans_spread_across_files(setup_mcp, health_data, session):
    """The explicit file_spread view spreads the cap without changing rank.

    ``deficit_by_path`` is a *file* property, so a pure deficit sort puts every
    plan on the worst file ahead of every plan on the second worst. Measured on
    this repo's own index before the fix, asking for the top 8 plans returned 8
    plans on a single file out of 1,903 — an agent asking "what should I
    refactor?" got no view of the repo at all. Filed as C4.
    """
    from repowise.server.mcp_server import get_health

    # Six plans on the worst file, two on the other. Impact descends within the
    # worst file so a deficit-then-impact sort would take all six of them first.
    await _seed_plans(
        session,
        health_data,
        [
            {
                "file_path": "src/auth/service.py",
                "target_symbol": f"worst_{i}",
                "source_biomarker": "complex_method",
                "impact_delta": 9.0 - i,
            }
            for i in range(6)
        ]
        + [
            {
                "file_path": "src/utils/helpers.py",
                "target_symbol": f"other_{i}",
                "source_biomarker": "complex_method",
                "impact_delta": 0.1,
            }
            for i in range(2)
        ],
    )

    plans = (
        await get_health(
            include=["refactoring"],
            only=["refactoring_plans"],
            limit=4,
            refactoring_view="file_spread",
        )
    )["refactoring_plans"]
    assert len(plans) == 4
    # Both files are represented rather than the worst file owning the list.
    assert len({p["file_path"] for p in plans}) == 2
    # The worst file still leads — spreading reorders within the cap, it does
    # not demote the file the directive names.
    assert plans[0]["file_path"] == "src/auth/service.py"
    # Within a file, the higher-impact plan still comes first.
    worst = [p["target_symbol"] for p in plans if p["file_path"] == "src/auth/service.py"]
    assert worst == sorted(worst, key=lambda t: int(t.split("_")[1]))


@pytest.mark.asyncio
async def test_refactoring_spread_is_exhaustive_when_one_file_has_them_all(
    setup_mcp, health_data, session
):
    """One file holding every plan must still fill the cap — the round-robin
    drains a file's queue rather than capping it at one row per file."""
    from repowise.server.mcp_server import get_health

    await _seed_plans(
        session,
        health_data,
        [
            {
                "file_path": "src/auth/service.py",
                "target_symbol": f"only_{i}",
                "source_biomarker": "complex_method",
                "impact_delta": 5.0 - i,
            }
            for i in range(5)
        ],
    )

    plans = (
        await get_health(include=["refactoring"], only=["refactoring_plans"], limit=4)
    )["refactoring_plans"]
    assert len(plans) == 4
    assert {p["file_path"] for p in plans} == {"src/auth/service.py"}


@pytest.mark.asyncio
async def test_directive_plan_via_is_projected(setup_mcp, health_data):
    """``plan_via`` must name a call that can actually complete.

    The bare ``include=['refactoring']`` measured 70,776 chars on this repo and
    fails the MCP token cap: five ranked lists at the default limit compose, and
    ``include`` adds a block without subtracting the dashboard. The directive
    told an agent to make the one call it could not finish.
    """
    from repowise.server.mcp_server import get_health

    directive = (await get_health(only=["directive"]))["directive"]
    assert "only=['refactoring_plans']" in directive["plan_via"]


@pytest.mark.asyncio
async def test_only_projection_keeps_unresolved(setup_mcp, health_data):
    """A typo'd target stays named however the response is projected.

    Regression: ``only`` kept ``mode`` and its named keys and dropped everything
    else, including ``unresolved`` — so ``targets=["does/not/exist.py"],
    only=["metrics"]`` came back as an empty ``metrics`` list and nothing else,
    which is exactly the "an empty result reads as healthy" failure A1 exists to
    close. A caller who has to ask for the error report in order to see it does
    not have an error report.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["does/not/exist.py"], only=["metrics"])
    assert result["metrics"] == []
    assert result["unresolved"] == [{"target": "does/not/exist.py", "reason": "no_such_path"}]


@pytest.mark.asyncio
async def test_only_projection_keeps_known_modules(setup_mcp, health_data):
    """``known_modules`` is the recovery path for a bad ``module:`` and rides along."""
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["module:nope"], only=["metrics"])
    assert result["unresolved"] == [{"target": "module:nope", "reason": "no_such_module"}]
    assert "auth" in result["known_modules"]


@pytest.mark.asyncio
async def test_metric_rows_drop_the_duplicated_defect_score(setup_mcp, health_data):
    """``score`` *is* the defect dimension; two names for it earned nothing.

    ``engine.py`` sets ``score`` and ``defect_score`` from the same
    ``scores["defect"]`` value — measured on the live index, 3,314 of 3,314 rows
    had them equal and none was NULL. The cost was never bytes, it was an agent
    reading the source to decide which of the two to rank on (the answer is
    neither: it is ``weighted_deficit``).
    """
    from repowise.server.mcp_server import get_health

    row = (await get_health(targets=["src/auth/service.py"]))["metrics"][0]
    assert "defect_score" not in row
    assert row["score"] == 4.5
    # The other two pillars are genuinely distinct numbers and stay.
    assert row["maintainability_score"] == 6.0
    assert row["performance_score"] == 9.0
    for block in ("worst_files", "high_leverage_files"):
        projected = await get_health(only=[block])
        assert all("defect_score" not in m for m in projected[block])


@pytest.mark.asyncio
async def test_high_leverage_rows_carry_share_of_repo_gap(setup_mcp, health_data):
    """``weighted_deficit`` is score-points x NLOC — unusable without a denominator."""
    from repowise.server.mcp_server import get_health

    result = await get_health()
    gap = result["gap_analysis"]["weighted_gross_gap_points"]
    rows = result["high_leverage_files"]
    assert rows, "fixture must have at least one file below the healthy band"
    for row in rows:
        expected = round(100.0 * row["weighted_deficit"] / gap, 1)
        # Shares are bounded by 100% and sum to 100% by construction: the gross
        # deficit of all below-target files is the denominator (issue #1437).
        assert row["share_of_repo_gap_pct"] == pytest.approx(expected, abs=0.11)
    # The lead file's share is the same number the directive quotes. Not exact
    # equality: `_directive` rounds `recovers_points` to an integer before
    # dividing, the row divides the raw deficit, so the two agree to the
    # rounding and not below it. (Flagged in review as a fixture coincidence —
    # the fixture's deficit happens to be whole, which would have hidden a real
    # divergence.)
    assert rows[0]["share_of_repo_gap_pct"] == pytest.approx(
        result["directive"]["share_of_repo_gap_pct"], abs=0.1
    )


@pytest.fixture
async def gradient_health_data(session, populated_db: str) -> str:
    """One file whose largest finding is the continuous coverage gradient.

    Mirrors the live shape: the gradient out-weighs every discrete finding on
    the file, so a plain max-impact pick names it.
    """
    from repowise.core.persistence.crud import save_health_findings, save_health_metrics

    rid = populated_db
    await save_health_metrics(
        session,
        rid,
        [
            {
                "file_path": "src/wide.py",
                "score": 3.0,
                "max_ccn": 20,
                "max_nesting": 4,
                "nloc": 400,
                "has_test_file": True,
                "module": "src",
                "line_coverage_pct": 30.0,
            }
        ],
    )
    await save_health_findings(
        session,
        rid,
        [
            {
                "file_path": "src/wide.py",
                "biomarker_type": "coverage_gradient",
                "severity": "high",
                "function_name": None,
                "line_start": None,
                "line_end": None,
                "details": {},
                "health_impact": 2.8,
                "reason": "70% of lines uncovered (30% line coverage)",
            },
            {
                "file_path": "src/wide.py",
                "biomarker_type": "nested_complexity",
                "severity": "medium",
                "function_name": "run",
                "line_start": 10,
                "line_end": 90,
                "details": {},
                "health_impact": 1.1,
                "reason": "run nests 4 levels deep",
            },
        ],
    )
    return rid


@pytest.mark.asyncio
async def test_primary_biomarker_prefers_a_discrete_cause(setup_mcp, gradient_health_data):
    """The headline must say why *this* file, not what is true of every file.

    ``coverage_gradient`` is a continuous deduction — it fires on every file
    that has coverage data at all — so on a repo with coverage it wins the
    max-impact tiebreak nearly everywhere. Measured on the live index before
    this change it led 22 of the top 50 ``worst_files`` and 14 of the top 50
    ``high_leverage_files``; after, it leads none of either. Its magnitude is
    real and still counted in ``total_deduction``; it just stops being the
    answer to "why this file".
    """
    from repowise.server.mcp_server import get_health

    row = (await get_health(targets=["src/wide.py"]))["metrics"][0]
    assert row["primary_biomarker"] == "nested_complexity"
    assert row["primary_reason"] == "run nests 4 levels deep"
    # The gradient is still the larger deduction and is still summed in.
    assert row["total_deduction"] == pytest.approx(3.9)
    dash = await get_health(only=["worst_files"])
    worst = next(m for m in dash["worst_files"] if m["file_path"] == "src/wide.py")
    assert worst["primary_biomarker"] == "nested_complexity"


@pytest.mark.asyncio
async def test_a_gradient_only_file_still_leads_with_the_gradient(setup_mcp, session, populated_db):
    """Preferring discrete must not blank the headline when there is nothing else."""
    from repowise.core.persistence.crud import save_health_findings, save_health_metrics
    from repowise.server.mcp_server import get_health

    await save_health_metrics(
        session,
        populated_db,
        [{"file_path": "src/only.py", "score": 6.0, "max_ccn": 2, "max_nesting": 1, "nloc": 80}],
    )
    await save_health_findings(
        session,
        populated_db,
        [
            {
                "file_path": "src/only.py",
                "biomarker_type": "coverage_gradient",
                "severity": "medium",
                "function_name": None,
                "line_start": None,
                "line_end": None,
                "details": {},
                "health_impact": 2.0,
                "reason": "50% of lines uncovered",
            }
        ],
    )
    row = (await get_health(targets=["src/only.py"]))["metrics"][0]
    assert row["primary_biomarker"] == "coverage_gradient"


@pytest.mark.asyncio
async def test_kpis_report_how_much_of_the_headline_is_non_code(setup_mcp, session, populated_db):
    """Markdown and JSON rows score a mechanical 10.0 and lift the average.

    No biomarker walks a non-code file, so its 10.0 means "nothing looked at
    this" — the same fabricated-10.0 problem the perf pillar already surfaces
    rather than hides. Measured on the live index: 233 of 3,314 rows are
    non-code, 221 of them score exactly 10.0, and they lift ``average_health``
    from 7.31 to 7.47, so a repo can raise its score by adding documentation.
    Surfaced rather than subtracted — ``average_health`` is what the badge, the
    snapshots and the web UI read, and redefining it here alone would make this
    tool disagree with all of them.
    """
    from repowise.core.persistence.crud import save_health_metrics
    from repowise.server.mcp_server import get_health

    await save_health_metrics(
        session,
        populated_db,
        [
            {"file_path": "src/auth/service.py", "score": 4.0, "nloc": 100, "max_ccn": 9},
            # No graph node → no language → not in LANGUAGE_MAPS → non-code.
            {"file_path": "docs/CHANGELOG.md", "score": 10.0, "nloc": 100, "max_ccn": 0},
        ],
    )
    kpis = (await get_health())["kpis"]
    assert kpis["file_count"] == 2
    assert kpis["non_code_files"] == 1
    assert kpis["average_health"] == 7.0
    assert kpis["average_health_code_only"] == 4.0


@pytest.mark.asyncio
async def test_non_code_split_is_gated_on_the_language_read(setup_mcp, health_data):
    """The split rides the language map ``kpis`` already reads — it adds no query.

    So it appears only where that read happens: dashboard mode with ``kpis``
    surviving the projection. ``only=["directive"]`` stays the cheapest useful
    call, and targeted mode (which serves no ``kpis`` block at all) is unchanged.
    """
    from repowise.server.mcp_server import get_health

    assert "kpis" not in await get_health(targets=["src/auth/service.py"])
    assert "kpis" not in await get_health(only=["directive"])
    assert "non_code_files" in (await get_health())["kpis"]


@pytest.mark.asyncio
async def test_default_dashboard_is_compact_before_final_delivery(
    setup_mcp, health_data, monkeypatch
):
    """Five ranked lists at the default limit compose past the host's cap.

    Past ``MAX_MCP_OUTPUT_TOKENS`` the host *rejects* the whole result with an
    isError — the agent loses the answer entirely — so per-list caps that are
    each reasonable are not enough; something has to bound the sum. Measured on
    the live index at ``limit=50, include=['refactoring']``: 60,299 chars before
    the guard, trimmed to fit after, with every cut named.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health()
    assert len(json.dumps(result, separators=(",", ":"), default=str)) <= 24_000
    assert "truncated_to_fit" not in result["_meta"]
    assert result["directive"]["fix_first"]


@pytest.mark.asyncio
async def test_a_response_that_fits_is_not_trimmed(setup_mcp, health_data):
    """No trim, no marker — the guard must be invisible on every normal call."""
    from repowise.server.mcp_server import get_health

    meta = (await get_health(include=["refactoring"]))["_meta"]
    assert "truncated_to_fit" not in meta
    assert "truncated_recovery" not in meta


@pytest.mark.asyncio
async def test_tool_local_budget_does_not_preempt_final_delivery(
    setup_mcp, health_data, monkeypatch
):
    """The blocks that let a caller recover must survive the cut that caused it."""
    from repowise.server.mcp_server import get_health

    monkeypatch.setenv("MAX_MCP_OUTPUT_TOKENS", "400")
    result = await get_health()
    assert result["directive"]["fix_first"]
    assert "truncated_to_fit" not in result["_meta"]


@pytest.mark.asyncio
async def test_meta_reports_the_commit_health_was_scored_at(setup_mcp, session, populated_db):
    """``indexed_commit`` describes the index; health is a separate pass.

    A response could show a current index beside scores computed several commits
    earlier and look entirely fresh. ``health_analyzed_at`` said *when*; nothing
    said *which commit*.
    """
    from repowise.core.persistence.crud import save_health_metrics
    from repowise.server.mcp_server import get_health

    await save_health_metrics(
        session,
        populated_db,
        [{"file_path": "src/auth/service.py", "score": 4.0, "nloc": 100, "max_ccn": 9}],
        analyzed_commit="c" * 40,
    )
    meta = (await get_health())["_meta"]
    assert meta["health_analyzed_commit"] == "c" * 12
    # One pass, one commit — no need to warn about a mixed table.
    assert "health_analyzed_commits_distinct" not in meta


@pytest.mark.asyncio
async def test_meta_admits_when_the_table_holds_two_scoring_passes(
    setup_mcp, session, populated_db
):
    """The incremental path rewrites only changed files, so the table can be mixed.

    Reporting one SHA for all of it would be a claim the read cannot support —
    which is why the column is per row rather than per repo.
    """
    from repowise.core.persistence.crud import save_health_metrics, upsert_health_metrics
    from repowise.server.mcp_server import get_health

    await save_health_metrics(
        session,
        populated_db,
        [
            {"file_path": "src/auth/service.py", "score": 4.0, "nloc": 100, "max_ccn": 9},
            {"file_path": "src/db/models.py", "score": 9.0, "nloc": 50, "max_ccn": 2},
        ],
        analyzed_commit="a" * 40,
    )
    await upsert_health_metrics(
        session,
        populated_db,
        [{"file_path": "src/auth/service.py", "score": 3.0, "nloc": 100, "max_ccn": 9}],
        analyzed_commit="b" * 40,
    )
    meta = (await get_health())["_meta"]
    assert meta["health_analyzed_commit"] == "b" * 12  # the newest pass
    assert meta["health_analyzed_commits_distinct"] == 2


@pytest.mark.asyncio
async def test_an_unstamped_upsert_does_not_erase_an_existing_commit(
    setup_mcp, session, populated_db
):
    """A caller that does not track the sha must not wipe one that does."""
    from repowise.core.persistence.crud import save_health_metrics, upsert_health_metrics
    from repowise.server.mcp_server import get_health

    await save_health_metrics(
        session,
        populated_db,
        [{"file_path": "src/auth/service.py", "score": 4.0, "nloc": 100, "max_ccn": 9}],
        analyzed_commit="a" * 40,
    )
    await upsert_health_metrics(
        session,
        populated_db,
        [{"file_path": "src/auth/service.py", "score": 3.0, "nloc": 100, "max_ccn": 9}],
    )
    assert (await get_health())["_meta"]["health_analyzed_commit"] == "a" * 12


@pytest.mark.asyncio
async def test_meta_omits_the_commit_when_no_row_records_one(setup_mcp, health_data):
    """NULL reads as "not recorded", never as "current"."""
    from repowise.server.mcp_server import get_health

    meta = (await get_health())["_meta"]
    assert "health_analyzed_commit" not in meta
    assert isinstance(meta["health_analyzed_at"], str)
    assert meta["health_analysis"]["source"] == "stored_health_analysis"
    assert meta["health_analysis"]["recomputed_this_call"] is False
    assert meta["health_analysis"]["live_verification"] == {
        "basis": "unavailable",
        "source_bytes_verified": False,
    }
    analysis = meta["health_analysis"]
    assert analysis["status"] == "degraded"
    assert analysis["reason"] == "analysis_commit_not_recorded"
    assert analysis["refresh"] == {
        "command": "repowise update",
        "precondition": "commit health-relevant working-tree changes first",
        "required_before_comparison": True,
    }


@pytest.mark.asyncio
async def test_health_semantics_survive_narrow_projection(setup_mcp, health_data):
    from repowise.server.mcp_server import get_health

    broad = await get_health()
    narrow = await get_health(only=["directive"])
    assert narrow["directive"] == broad["directive"]
    assert narrow["_meta"]["health_semantics"] == broad["_meta"]["health_semantics"]
    contract = narrow["_meta"]["health_semantics"]["weighted_deficit_points"]
    assert contract["unit"] == "health_score_points_x_nloc"
    assert contract["denominator"] == "gap_analysis.weighted_gross_gap_points"
    assert contract["scale"] == {"minimum": 0, "maximum": None, "normalized": False}
    assert "not a probability" in contract["interpretation"]


@pytest.mark.asyncio
async def test_requested_empty_plans_explain_real_pipeline_state(setup_mcp, health_data):
    from repowise.server.mcp_server import get_health

    healthy = await get_health(
        targets=["src/db/models.py"],
        include=["refactoring"],
        only=["metrics", "findings", "refactoring_plans"],
    )
    assert healthy["findings_total"] == 0
    assert healthy["refactoring_plans"] == []
    assert healthy["refactoring_plans_status"]["reason"] == "no_applicable_findings"

    unsupported = await get_health(
        targets=["src/auth/service.py"],
        include=["refactoring"],
        only=["findings", "refactoring_plans"],
    )
    assert unsupported["findings_total"] > 0
    assert unsupported["refactoring_plans"] == []
    status = unsupported["refactoring_plans_status"]
    assert status["state"] == "indeterminate"
    assert status["reason"] == "plan_analysis_indeterminate"
    assert status["possible_causes"] == [
        "no_supported_structured_transformation",
        "refactoring_detector_disabled_or_failed",
    ]
    assert status["next_action"] == {
        "tool": "get_symbol",
        "arguments": {"symbol_id": "src/auth/service.py:10-80"},
    }


@pytest.mark.asyncio
async def test_plan_status_is_omitted_when_projection_did_not_request_plans(
    setup_mcp, health_data
):
    from repowise.server.mcp_server import get_health

    result = await get_health(include=["refactoring"], only=["suggestion_legend"])
    assert "refactoring_plans" not in result
    assert "refactoring_plans_status" not in result


@pytest.mark.asyncio
async def test_unresolved_and_missing_analysis_never_read_as_healthy(
    setup_mcp, populated_db
):
    from repowise.server.mcp_server import get_health

    unresolved = await get_health(
        targets=["does/not/exist.py"],
        include=["refactoring"],
        only=["metrics", "refactoring_plans"],
    )
    assert unresolved["unresolved"] == [
        {"target": "does/not/exist.py", "reason": "no_such_path"}
    ]
    assert unresolved["refactoring_plans_status"]["reason"] == "no_eligible_targets"

    missing = await get_health(
        include=["refactoring"],
        only=["directive", "kpis", "refactoring_plans"],
    )
    assert missing["directive"] is None
    assert missing["kpis"]["average_health"] is None
    assert missing["kpis"]["analysis_status"] == "unavailable"
    assert missing["refactoring_plans_status"]["reason"] == "analysis_unavailable"
    assert missing["_meta"]["health_analysis"]["status"] == "unavailable"


def test_only_docstring_does_not_overclaim_the_aliases():
    """The docstring listed the ``include`` names as ``only`` aliases. Three are.

    ``performance`` / ``defect`` / ``maintainability`` are dimension filters with
    no top-level key, so they land in ``unknown_only_keys`` — a caller typing one
    out of the docstring gets an empty projection and no explanation.
    """
    from repowise.server.mcp_server.tool_health import _ONLY_ALIASES, get_health

    doc = get_health.__doc__ or ""
    only_section = doc.split("only:", 1)[1].split("repo:", 1)[0]
    assert set(_ONLY_ALIASES) == {"biomarkers", "accuracy", "refactoring"}
    for name in _ONLY_ALIASES:
        assert name in only_section
    # And it says plainly that the dimension names are not aliases.
    for name in ("performance", "defect", "maintainability"):
        assert name in only_section
    assert "do not" in only_section


@pytest.mark.asyncio
async def test_module_metrics_obey_limit_and_lead_with_rollup(
    setup_mcp, health_data, monkeypatch
):
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["module:auth", "module:db"], limit=1)
    assert list(result)[:3] == ["mode", "targets", "modules"]
    assert result["modules_total"] == 2
    assert result["modules_emitted"] == 2
    assert result["metrics_total"] == 2
    assert result["metrics_emitted"] == 1
    assert result["metrics_reduced_reason"] == "limit"


@pytest.mark.asyncio
async def test_module_metrics_limit_zero_returns_rollup_and_totals(
    setup_mcp, health_data, monkeypatch
):
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["module:auth", "module:db"], limit=0)
    assert result["metrics"] == []
    assert result["metrics_total"] == 2
    assert result["metrics_emitted"] == 0
    assert len(result["modules"]) == 2
    assert result["modules_total"] == 2
    assert result["modules_emitted"] == 2
    assert "modules_reduced_reason" not in result


@pytest.mark.asyncio
async def test_shared_budget_contract_owns_get_health_final_delivery(
    setup_mcp, health_data, monkeypatch
):
    """``coverage.files`` is not a top-level key; a flat scan never finds it."""
    from repowise.server.mcp_server import get_health
    from repowise.server.mcp_server._budget import budgeted_tool_names

    assert "get_health" in budgeted_tool_names()
    result = await get_health(include=["coverage"], targets=["src/auth/service.py"], limit=0)
    assert result["coverage"]["files"] == []
    assert result["coverage"]["files_total"] == 0
    assert result["coverage"]["files_emitted"] == 0
    assert "files_reduced_reason" not in result["coverage"]


@pytest.mark.asyncio
async def test_large_module_metrics_obey_every_limit(
    setup_mcp, health_data, session
):
    import uuid

    from repowise.core.persistence.models import HealthFileMetric
    from repowise.server.mcp_server import get_health

    for index in range(60):
        session.add(
            HealthFileMetric(
                id=str(uuid.uuid4()),
                repository_id=health_data,
                file_path=f"src/large/file_{index:02d}.py",
                module="large",
                score=1.0 + index / 100,
                max_ccn=10 + index,
                max_nesting=2,
                nloc=100 + index,
                has_test_file=False,
            )
        )
    await session.flush()

    for limit in (0, 1, 20, 50):
        result = await get_health(targets=["module:large"], only=["metrics"], limit=limit)
        assert result["metrics_total"] == 60
        assert result["metrics_emitted"] == limit
        assert len(result["metrics"]) == limit
        assert result["metrics_reduced_reason"] == "limit"
        recovery = result["recovery"]["metrics"]
        assert f"cursor={limit}" in recovery["call"]
        first_page_limit = min(60 - limit, 50)
        assert f"limit={first_page_limit}" in recovery["call"]
        dropped_paths = {f"src/large/file_{index:02d}.py" for index in range(limit, 60)}
        emitted_paths = {row["file_path"] for row in result["metrics"]}
        recovered_paths = set()
        next_cursor = limit
        while next_cursor < 60:
            page_limit = min(60 - next_cursor, 50)
            recovered = await get_health(
                targets=["module:large"],
                only=["metrics"],
                cursor=next_cursor,
                limit=page_limit,
            )
            page_paths = {row["file_path"] for row in recovered["metrics"]}
            assert len(page_paths) == page_limit
            recovered_paths |= page_paths
            next_cursor += page_limit
        assert recovered_paths == dropped_paths
        assert recovered_paths.isdisjoint(emitted_paths)


@pytest.mark.asyncio
async def test_every_growing_collection_has_total_and_emitted_counts(
    setup_mcp, health_data
):
    from repowise.server.mcp_server import get_health

    result = await get_health(include=["performance", "refactoring"], limit=2)

    def assert_counted(value):
        if isinstance(value, list):
            for item in value:
                assert_counted(item)
            return
        if not isinstance(value, dict):
            return
        for key, child in value.items():
            if isinstance(child, list):
                assert f"{key}_total" in value, key
                assert f"{key}_emitted" in value, key
                assert value[f"{key}_total"] >= value[f"{key}_emitted"] == len(child)
            assert_counted(child)

    assert_counted(result)


@pytest.mark.asyncio
async def test_the_ranked_findings_leave_performance_out_but_asking_returns_it(
    setup_mcp, health_data
):
    """A ranking by impact is not a place to put rows that carry none.

    Every performance finding scores zero impact by construction, so in a mixed
    list it sorts below every defect row and reads as a tail rather than as a
    different unit. The dimension answers through its own blocks, and naming it
    in ``include`` still returns the rows.
    """
    from repowise.server.mcp_server import get_health

    default = await get_health(only=["top_findings"])
    assert all(f["dimension"] != "performance" for f in default["top_findings"])

    asked = await get_health(include=["performance"], only=["top_findings"])
    assert any(f["dimension"] == "performance" for f in asked["top_findings"])
