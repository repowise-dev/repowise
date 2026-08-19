"""Unit tests for repowise MCP server tools.

Tests all 9 MCP tools using an in-memory SQLite database with pre-populated
test data, mirroring the conftest pattern from the REST API tests.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_risk_single_target(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/service.py"])
    targets = result["targets"]
    assert "src/auth/service.py" in targets
    t = targets["src/auth/service.py"]
    assert t["hotspot_score"] == 0.92
    assert t["dependents_count"] >= 1  # middleware imports it
    assert len(t["co_change_partners"]) == 2
    assert t["primary_owner"] == "Alice"
    assert t["owner_pct"] == 0.65
    assert "risk_summary" in t
    assert "hotspot score" in t["risk_summary"]

    # Trend: 30d=3, 90d=8 → baseline_rate=0.083, recent=0.1 → stable
    assert t["trend"] in ("increasing", "stable", "decreasing")

    # Risk type: churn_percentile=0.92, no fix keywords → churn-heavy
    assert t["risk_type"] == "churn-heavy"

    # Impact surface: middleware.py depends on service.py
    assert len(t["impact_surface"]) >= 1
    impact_files = [s["file_path"] for s in t["impact_surface"]]
    assert "src/auth/middleware.py" in impact_files
    # Each entry has pagerank and is_entry_point
    for s in t["impact_surface"]:
        assert "pagerank" in s
        assert "is_entry_point" in s


@pytest.mark.asyncio
async def test_get_risk_multiple_targets(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/service.py", "src/db/models.py"])
    targets = result["targets"]
    assert len(targets) == 2
    assert "global_hotspots" in result
    # Both targets should have trend and risk_type
    for t in targets.values():
        assert "trend" in t
        assert "risk_type" in t


@pytest.mark.asyncio
async def test_get_risk_global_hotspots_exclude_targets(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/service.py"])
    # service.py is a hotspot but should NOT appear in global_hotspots
    for h in result["global_hotspots"]:
        assert h["file_path"] != "src/auth/service.py"


@pytest.mark.asyncio
async def test_get_risk_no_git_metadata(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/middleware.py"])
    t = result["targets"]["src/auth/middleware.py"]
    assert t["hotspot_score"] == 0.0  # No git metadata for this file
    assert t["trend"] == "unknown"
    assert "risk_summary" in t
    # Impact surface and risk_type still computed from graph data
    assert "risk_type" in t
    assert "impact_surface" in t


@pytest.mark.asyncio
async def test_get_risk_stable_file(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/db/models.py"])
    t = result["targets"]["src/db/models.py"]
    # 0 commits in 30d and 90d → stable
    assert t["trend"] == "stable"
    # churn_percentile=0.15, dep_count=1, no fix keywords → stable
    assert t["risk_type"] == "stable"


@pytest.mark.asyncio
async def test_get_risk_pr_directive_splits_test_breakage(setup_mcp):
    """PR mode splits test-file fallout out of will_break into will_break_tests (#672)."""
    from repowise.server.mcp_server import get_risk

    # Pass changed_files to trigger PR mode + blast-radius directive.
    result = await get_risk(["src/auth/service.py"], changed_files=["src/auth/service.py"])
    directive = result["directive"]

    # middleware.py imports service.py → production breakage.
    assert "src/auth/middleware.py" in directive["will_break"]
    assert "src/auth/middleware.py" not in directive["will_break_tests"]

    # test_service.py imports service.py but is is_test=True → segmented out.
    assert "tests/test_service.py" in directive["will_break_tests"]
    assert "tests/test_service.py" not in directive["will_break"]

    # Summary reflects the test count.
    assert "test(s) likely broken" in directive["summary"]


@pytest.mark.asyncio
async def test_get_risk_pr_directive_surfaces_coverage_backed_tests_to_run(setup_mcp, session):
    """PR directive carries coverage-backed tests_to_run from the per-test map."""
    from repowise.core.analysis.health.coverage import TestCoverage
    from repowise.core.persistence.crud import save_test_coverage
    from repowise.server.mcp_server import get_risk

    # Seed a per-test map: two tests execute service.py.
    await save_test_coverage(
        session,
        "repo1",
        [
            TestCoverage(
                test_id="tests/test_service.py::test_login",
                file_path="src/auth/service.py",
                covered_lines=[1, 2],
                source_format="coverage.py",
                test_file="tests/test_service.py",
            ),
            TestCoverage(
                test_id="tests/test_service.py::test_logout",
                file_path="src/auth/service.py",
                covered_lines=[3, 4],
                source_format="coverage.py",
                test_file="tests/test_service.py",
            ),
        ],
        source_format="coverage.py",
    )
    await session.flush()

    result = await get_risk(["src/auth/service.py"], changed_files=["src/auth/service.py"])
    directive = result["directive"]

    assert directive["tests_to_run"] == [
        "tests/test_service.py::test_login",
        "tests/test_service.py::test_logout",
    ]
    # The graph also reaches this file, and must not dilute a measured answer.
    assert directive["tests_to_run_basis"] == "measured"
    assert "coverage-backed test(s) guard the change" in directive["summary"]


@pytest.mark.asyncio
async def test_get_risk_pr_directive_falls_back_to_the_graph_without_a_map(setup_mcp):
    """No per-test map -> the import graph answers, labelled as inferred.

    The fixture records ``tests/test_service.py`` importing ``service.py``. That
    is not proof it executes it, so the ids are test *files* and the basis says
    ``inferred``; what it replaces is an empty list on every repo that has never
    ingested a coverage report.
    """
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/service.py"], changed_files=["src/auth/service.py"])
    directive = result["directive"]

    assert directive["tests_to_run"] == ["tests/test_service.py"]
    assert directive["tests_to_run_basis"] == "inferred"
    assert "inferred, not coverage-proven" in directive["summary"]
    assert "coverage-backed test(s) guard the change" not in directive["summary"]


@pytest.mark.asyncio
async def test_get_risk_pr_directive_names_no_tests_when_nothing_reaches(setup_mcp):
    """Neither map nor graph -> an empty list and a ``none`` basis, not a guess."""
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/db/models.py"], changed_files=["src/db/models.py"])
    directive = result["directive"]

    assert directive["tests_to_run"] == []
    assert directive["tests_to_run_basis"] == "none"


# ---- _classify_risk_type small-team calibration (issue #361) ---------------


def _bus_factor_meta():
    from types import SimpleNamespace

    return SimpleNamespace(
        significant_commits_json="[]",
        churn_percentile=0.3,
        bus_factor=1,
        commit_count_total=40,
        is_hotspot=False,
    )


def test_classify_bus_factor_risk_on_normal_team():
    from repowise.server.mcp_server.tool_risk import _classify_risk_type

    assert _classify_risk_type(_bus_factor_meta(), dep_count=1, team_size=8) == "bus-factor-risk"


def test_classify_bus_factor_suppressed_on_small_team():
    """A single-author file is the expected shape of a 1-3 person repo —
    not a bus-factor warning unless the file is hotspot-active."""
    from repowise.server.mcp_server.tool_risk import _classify_risk_type

    assert _classify_risk_type(_bus_factor_meta(), dep_count=1, team_size=2) == "stable"


def test_classify_bus_factor_kept_on_small_team_hotspot():
    from repowise.server.mcp_server.tool_risk import _classify_risk_type

    meta = _bus_factor_meta()
    meta.is_hotspot = True
    assert _classify_risk_type(meta, dep_count=1, team_size=2) == "bus-factor-risk"


def test_classify_bus_factor_unknown_team_size_keeps_behaviour():
    from repowise.server.mcp_server.tool_risk import _classify_risk_type

    assert _classify_risk_type(_bus_factor_meta(), dep_count=1, team_size=None) == "bus-factor-risk"


@pytest.mark.parametrize(
    ("source", "test"),
    [
        ("lib/user.dart", "test/user_test.dart"),
        ("lib/user.ex", "test/user_test.exs"),
        ("src/user.rs", "tests/user_test.rs"),
    ],
)
def test_health_filename_heuristic_supports_suffix_test_conventions(source, test):
    from repowise.core.analysis.health.engine import _has_paired_test_file

    assert _has_paired_test_file(source, {test.rsplit("/", 1)[-1]})


@pytest.mark.asyncio
async def test_test_gap_uses_health_filename_heuristic(session, repo_id):
    """Health and risk agree that suffixed test names are not exact pairs."""
    from repowise.core.analysis.health.engine import _has_paired_test_file
    from repowise.core.persistence.models import GraphNode
    from repowise.server.mcp_server.tool_risk.assessment import _check_test_gap

    session.add(
        GraphNode(
            id="gn-decoy",
            repository_id=repo_id,
            node_id="tests/test_my_module_strategies.py",
            node_type="file",
            language="python",
            is_test=True,
        )
    )
    await session.flush()

    assert not _has_paired_test_file("src/my_module.py", {"test_my_module_strategies.py"})
    assert await _check_test_gap(session, repo_id, "src/my_module.py") is True

    session.add(
        GraphNode(
            id="gn-real",
            repository_id=repo_id,
            node_id="tests/test_my_module.py",
            node_type="file",
            language="python",
            is_test=True,
        )
    )
    await session.flush()
    assert await _check_test_gap(session, repo_id, "src/my_module.py") is False
