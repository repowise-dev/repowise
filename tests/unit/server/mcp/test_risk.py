"""Unit tests for repowise MCP server tools.

Tests all 9 MCP tools using an in-memory SQLite database with pre-populated
test data, mirroring the conftest pattern from the REST API tests.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_risk_single_target(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/service.py"], include=["graph", "churn"])
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

    result = await get_risk(["src/auth/service.py", "src/db/models.py"], include=["churn"])
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

    result = await get_risk(["src/auth/service.py", "src/db/models.py"])
    # service.py is a hotspot but should NOT appear in global_hotspots
    for h in result["global_hotspots"]:
        assert h["file_path"] != "src/auth/service.py"


@pytest.mark.asyncio
async def test_get_risk_normalizes_target_path(setup_mcp):
    """#1279: git_metadata row lookup must survive non-POSIX target forms.

    Callers hand paths over with backslashes, a leading ``./``, or a trailing
    separator. get_risk matches git_metadata.file_path by exact equality, so
    each of these previously missed the row and reported the "no git metadata
    available" card (hotspot_score=0 / primary_owner=None / empty partners)
    even though the row exists.
    """
    from repowise.server.mcp_server import get_risk

    for target in ("src\\auth\\service.py", "./src/auth/service.py", "src/auth/service.py/"):
        result = await get_risk([target])
        # Response stays keyed by the caller's exact string.
        t = result["targets"][target]
        assert t["hotspot_score"] == 0.92, target
        assert t["primary_owner"] == "Alice", target
        assert len(t["co_change_partners"]) == 2, target
        assert "no git metadata available" not in t["risk_summary"], target
        # Trend: 30d=3, 90d=8 → stable.
        assert t["trend"] == "stable", target


@pytest.mark.asyncio
async def test_get_risk_repo_absolute_target_path(setup_mcp):
    """A repo-absolute target is made repo-relative before the lookup (#1279)."""
    from repowise.server.mcp_server import get_risk

    abs_target = "/tmp/test-repo/src/auth/service.py"
    result = await get_risk([abs_target])
    t = result["targets"][abs_target]
    assert t["hotspot_score"] == 0.92
    assert t["primary_owner"] == "Alice"
    assert len(t["co_change_partners"]) == 2


@pytest.mark.asyncio
async def test_get_risk_no_git_metadata(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/middleware.py"], include=["graph", "churn"])
    t = result["targets"]["src/auth/middleware.py"]
    assert t["hotspot_score"] == 0.0  # No git metadata for this file
    assert t["trend"] == "unknown"
    assert "risk_summary" in t
    # Impact surface and risk_type still computed from graph data
    assert "risk_type" in t
    assert "impact_surface" in t


@pytest.mark.parametrize(
    ("raw", "repo_root", "expected"),
    [
        ("src/auth/service.py", None, "src/auth/service.py"),
        ("src\\auth\\service.py", None, "src/auth/service.py"),
        ("./src/auth/service.py", None, "src/auth/service.py"),
        ("src/auth/service.py/", None, "src/auth/service.py"),
        ("src//auth//service.py", None, "src/auth/service.py"),
        (".github/workflows/ci.yml", None, ".github/workflows/ci.yml"),
        ("./.github/workflows/ci.yml", None, ".github/workflows/ci.yml"),
        (".claude/TRIAGE.md", None, ".claude/TRIAGE.md"),
        ("/tmp/test-repo/src/auth/service.py", "/tmp/test-repo", "src/auth/service.py"),
    ],
)
def test_normalize_target_path(raw, repo_root, expected):
    from repowise.server.mcp_server.tool_risk.assessment import normalize_target_path

    assert normalize_target_path(raw, repo_root=repo_root) == expected


@pytest.mark.asyncio
async def test_get_risk_stable_file(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/db/models.py"], include=["churn"])
    t = result["targets"]["src/db/models.py"]
    # 0 commits in 30d and 90d → stable
    assert t["trend"] == "stable"
    # churn_percentile=0.15, dep_count=1, no fix keywords → stable
    assert t["risk_type"] == "stable"


@pytest.mark.asyncio
async def test_get_risk_pr_directive_splits_test_breakage(setup_mcp):
    """PR mode splits test-file fallout out of may_break into may_break_tests (#672)."""
    from repowise.server.mcp_server import get_risk

    # Pass changed_files to trigger PR mode + blast-radius directive.
    result = await get_risk(["src/auth/service.py"], changed_files=["src/auth/service.py"])
    directive = result["directive"]

    # middleware.py imports service.py → production breakage.
    assert "src/auth/middleware.py" in directive["may_break"]
    assert "src/auth/middleware.py" not in directive["may_break_tests"]

    # test_service.py imports service.py but is is_test=True → segmented out.
    assert "tests/test_service.py" in directive["may_break_tests"]
    assert "tests/test_service.py" not in directive["may_break"]

    # Summary reflects the test count.
    assert "test(s) may break" in directive["summary"]

    # The savings estimator reads these lists by name; a rename that misses it
    # undercounts silently rather than raising.
    from repowise.server.mcp_server._savings.counterfactual import RISK_RELATED_FILE_KEYS

    assert set(RISK_RELATED_FILE_KEYS) <= set(directive)


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
    assert "2 measured" in directive["summary"]
    assert {row["basis"] for row in directive["test_recommendations"]} == {
        "measured",
        "inferred",
    }
    assert all(
        "basis" in evidence
        for row in directive["test_recommendations"]
        for evidence in row["evidence"]
    )


@pytest.mark.asyncio
async def test_get_risk_tests_to_run_ranks_by_files_reached(setup_mcp, session):
    """The test covering both changed files leads, despite sorting last."""
    from repowise.core.analysis.health.coverage import TestCoverage
    from repowise.core.persistence.crud import save_test_coverage
    from repowise.server.mcp_server import get_risk

    def _cov(test_id: str, path: str) -> TestCoverage:
        return TestCoverage(
            test_id=test_id,
            file_path=path,
            covered_lines=[1, 2],
            source_format="coverage.py",
            test_file=test_id.split("::")[0],
        )

    await save_test_coverage(
        session,
        "repo1",
        [
            # Sorts last alphabetically, reaches both changed files.
            _cov("tests/test_zeta.py::test_both", "src/auth/service.py"),
            _cov("tests/test_zeta.py::test_both", "src/auth/token.py"),
            # Sorts first alphabetically, reaches one.
            _cov("tests/test_alpha.py::test_one", "src/auth/service.py"),
        ],
        source_format="coverage.py",
    )
    await session.flush()

    result = await get_risk(
        ["src/auth/service.py"],
        changed_files=["src/auth/service.py", "src/auth/token.py"],
    )

    assert result["directive"]["tests_to_run"] == [
        "tests/test_zeta.py::test_both",
        "tests/test_alpha.py::test_one",
    ]
    assert "tests/test_service.py" in {
        row["test_id"] for row in result["directive"]["test_recommendations"]
    }


class TestRankTestsByReach:
    def test_more_files_reached_wins_over_alphabetical(self) -> None:
        from repowise.core.analysis.pr_blast import rank_tests_by_reach

        ranked = rank_tests_by_reach({"a.py": ["t_z", "t_both"], "b.py": ["t_a", "t_both"]})
        assert ranked == ["t_both", "t_a", "t_z"]

    def test_single_file_keeps_alphabetical_order(self) -> None:
        # Every test ties at one file reached, so the pre-existing ordering
        # of a single-file change is unchanged.
        from repowise.core.analysis.pr_blast import rank_tests_by_reach

        assert rank_tests_by_reach({"a.py": ["t_c", "t_a", "t_b"]}) == ["t_a", "t_b", "t_c"]

    def test_empty_mapping_is_empty(self) -> None:
        from repowise.core.analysis.pr_blast import rank_tests_by_reach

        assert rank_tests_by_reach({}) == []


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
    assert directive["missing_tests"] == []
    assert directive["coverage_analysis"]["status"] == "unavailable"
    assert "missing_tests is withheld" in directive["summary"]


@pytest.mark.asyncio
async def test_get_risk_pr_payload_serializes_directive_first(setup_mcp):
    """The exact external JSON order is actionable before any dossier."""
    import json

    from repowise.server.mcp_server import get_risk

    payload = await get_risk(["src/auth/service.py"], changed_files=["src/auth/service.py"])
    external = json.loads(json.dumps(payload))

    assert next(iter(payload)) == "directive"
    assert next(iter(external)) == "directive"
    assert list(external).index("directive") < list(external).index("targets")


@pytest.mark.asyncio
async def test_get_risk_test_compatibility_projection_cannot_contradict_typed_rows(setup_mcp):
    from repowise.server.mcp_server import get_risk

    directive = (await get_risk(["src/auth/service.py"], changed_files=["src/auth/service.py"]))[
        "directive"
    ]
    recommendations = directive["test_recommendations"]

    assert directive["tests_to_run"] == [row["test_id"] for row in recommendations]
    assert directive["tests_to_run_total"] == directive["test_recommendations_total"]
    assert directive["tests_to_run_emitted"] == len(recommendations)
    assert directive["test_recommendations_emitted"] == len(recommendations)
    assert all(row["basis"] in {"measured", "inferred"} for row in recommendations)
    assert all(row["basis"] in row["bases"] for row in recommendations)
    assert all(row["repository_id"] == "repo1" for row in recommendations)


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
        ("lib/user.rb", "spec/user_spec.rb"),
        ("src/user.cr", "spec/user_spec.cr"),
        ("lib/user.rb", "test/test_user.rb"),
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


@pytest.mark.asyncio
async def test_get_risk_omits_global_hotspots_for_a_single_target(setup_mcp):
    """Ambient orientation earns its place across targets, not on one named file."""
    from repowise.server.mcp_server import get_risk

    assert "global_hotspots" not in await get_risk(["src/auth/service.py"])
    assert "global_hotspots" in await get_risk(["src/auth/service.py", "src/db/models.py"])


@pytest.mark.asyncio
async def test_get_risk_gates_the_fields_an_agent_cannot_act_on(setup_mcp):
    """graph and churn blocks ship only when include asks for them."""
    from repowise.server.mcp_server import get_risk

    default = await get_risk(["src/auth/service.py"], changed_files=["src/auth/service.py"])
    card = default["targets"]["src/auth/service.py"]
    for key in ("impact_surface", "change_magnitude", "risk_type", "change_pattern"):
        assert key not in card
    assert "direct_risks" not in default["pr_blast_radius"]
    # The blocks that answer a question stay unconditional.
    for key in ("co_change_partners", "risk_summary"):
        assert key in card

    graph = await get_risk(
        ["src/auth/service.py"], changed_files=["src/auth/service.py"], include=["graph"]
    )
    assert "impact_surface" in graph["targets"]["src/auth/service.py"]
    assert "direct_risks" in graph["pr_blast_radius"]
    assert "change_magnitude" not in graph["targets"]["src/auth/service.py"]

    churn = await get_risk(["src/auth/service.py"], include=["churn"])
    churn_card = churn["targets"]["src/auth/service.py"]
    assert {"change_magnitude", "risk_type", "change_pattern"} <= set(churn_card)
    assert "impact_surface" not in churn_card


@pytest.mark.asyncio
async def test_get_risk_names_an_unknown_include_rather_than_applying_it(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/service.py"], include=["graph", "nonsense"])
    assert "impact_surface" in result["targets"]["src/auth/service.py"]
    assert result["ignored_arguments"] == [
        {"argument": "include", "values": ["nonsense"], "valid": ["churn", "graph", "scales"]}
    ]


@pytest.mark.asyncio
async def test_get_risk_directive_does_not_copy_the_analyzer_score(setup_mcp):
    """The structural heuristic lives in blast detail, not the directive."""
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/service.py"], changed_files=["src/auth/service.py"])

    assert "overall_risk_score" not in result["directive"]
    blast = result["pr_blast_radius"]
    assert blast["overall_risk_score"] == blast["structural_impact_score"]
    assert blast["overall_risk_score_compatibility"] == {
        "deprecated": True,
        "replacement": "structural_impact_score",
        "equivalent_value": True,
        "historical_meaning": "uncalibrated 0-10 structural blast-radius heuristic",
    }
    scale = blast["structural_impact_scale"]
    assert scale["calibration"]["status"] == "uncalibrated"
    assert scale["runtime_breakage_probability"] is False
    # Guard tier by default; the reference tier follows the caller's include.
    assert "component_fields" not in scale
    assert "risk_scales" not in result

    expanded = await get_risk(
        ["src/auth/service.py"], changed_files=["src/auth/service.py"], include=["scales"]
    )
    assert expanded["risk_scales"][0]["field"] == "targets.*.hotspot_score"
    assert expanded["pr_blast_radius"]["structural_impact_scale"]["component_fields"]


@pytest.mark.asyncio
async def test_get_risk_directive_points_at_the_full_run_list_when_capped(setup_mcp, session):
    """A capped tests_to_run says where the uncapped copy is."""
    from repowise.core.analysis.health.coverage import TestCoverage
    from repowise.core.persistence.crud import save_test_coverage
    from repowise.server.mcp_server import get_risk
    from repowise.server.mcp_server.tool_risk.directives import _TESTS_TO_RUN_LIMIT

    over_cap = _TESTS_TO_RUN_LIMIT + 3
    await save_test_coverage(
        session,
        "repo1",
        [
            TestCoverage(
                test_id=f"tests/test_{i}.py::test_it",
                file_path="src/auth/service.py",
                covered_lines=[1],
                source_format="coverage.py",
                test_file=f"tests/test_{i}.py",
            )
            for i in range(over_cap)
        ],
        source_format="coverage.py",
    )
    await session.flush()

    result = await get_risk(["src/auth/service.py"], changed_files=["src/auth/service.py"])
    directive = result["directive"]

    assert len(directive["tests_to_run"]) == _TESTS_TO_RUN_LIMIT
    assert f"Showing {_TESTS_TO_RUN_LIMIT} of {over_cap + 1}" in directive["summary"]
    assert directive["tests_to_run_total"] == over_cap
    assert directive["tests_to_run_emitted"] == _TESTS_TO_RUN_LIMIT
    assert directive["tests_to_run_reduced_reason"] == "construction_cap"
    assert directive["tests_to_run_truncated"] is True
    assert directive["test_recommendations_total"] == over_cap + 1
    assert directive["test_recommendations_reduced_reason"] == "construction_cap"
    assert "response omission marker" in directive["summary"]
    assert len(result["pr_blast_radius"]["guarding_tests"]["tests_to_run"]) == over_cap


@pytest.mark.asyncio
async def test_missing_tests_totals_use_full_precap_changed_file_population(setup_mcp, session):
    from repowise.core.analysis.health.coverage import TestCoverage
    from repowise.core.persistence.crud import save_test_coverage
    from repowise.core.persistence.models import GraphNode
    from repowise.server.mcp_server import get_risk

    changed = [f"src/sealed_gap_{index}.py" for index in range(5)]
    for index, path in enumerate(changed):
        session.add(
            GraphNode(
                id=f"sealed-gap-{index}",
                repository_id=setup_mcp,
                node_id=path,
                node_type="file",
                is_test=False,
            )
        )
    await save_test_coverage(
        session,
        setup_mcp,
        [
            TestCoverage(
                test_id="tests/test_seed.py::test_seed",
                file_path="src/coverage_seed.py",
                covered_lines=[1],
                source_format="coverage.py",
                test_file="tests/test_seed.py",
            )
        ],
        source_format="coverage.py",
    )
    await session.flush()

    result = await get_risk(["src/auth/service.py"], changed_files=changed)
    directive = result["directive"]

    assert directive["missing_tests"] == changed[:3]
    assert directive["missing_tests_total"] == 5
    assert directive["missing_tests_emitted"] == 3
    assert directive["missing_tests_reduced_reason"] == "construction_cap"
    assert directive["missing_tests_truncated"] is True
    assert directive["missing_tests_omitted"] == 2
