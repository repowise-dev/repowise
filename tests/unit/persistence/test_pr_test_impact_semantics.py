"""Sealed contract for PR test-impact evidence, availability, and identity."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.analysis.health.coverage import TestCoverage
from repowise.core.analysis.test_impact import analyze_test_impact
from repowise.core.exclusion import build_exclude_spec
from repowise.core.persistence.crud import save_test_coverage
from repowise.core.persistence.models import GraphEdge, GraphNode
from tests.unit.persistence.helpers import insert_repo

_FIXTURE = Path(__file__).parents[2] / "fixtures" / "mcp" / "pr_test_impact.json"


def _fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


async def _seed(session, repo_id: str, fixture: dict, *, with_coverage: bool = True) -> None:
    if with_coverage:
        await save_test_coverage(
            session,
            repo_id,
            [
                TestCoverage(
                    test_id=row["test_id"],
                    file_path=row["source_file"],
                    covered_lines=[1],
                    source_format="coverage.py",
                    test_file=row["test_file"],
                )
                for row in fixture["coverage"]
            ],
            source_format="coverage.py",
            ingested_commit_sha="fixture-head",
        )

    paths = set(fixture["changed_files"] + fixture["unavailable_changed_files"])
    paths.update(row["source_file"] for row in fixture["coverage"])
    paths.update(row["test_file"] for row in fixture["coverage"])
    paths.update(row["source_file"] for row in fixture["inferred"])
    paths.update(row["test_file"] for row in fixture["inferred"])
    relationship = fixture["relationship_semantics"]
    paths.update(
        [
            relationship["dependency"]["source"],
            relationship["dependency"]["target"],
            relationship["co_change"]["source"],
        ]
    )
    test_paths = {
        *(row["test_file"] for row in fixture["coverage"]),
        *(row["test_file"] for row in fixture["inferred"]),
    }
    for index, path in enumerate(sorted(paths)):
        session.add(
            GraphNode(
                id=f"{repo_id}-node-{index}",
                repository_id=repo_id,
                node_id=path,
                node_type="file",
                is_test=path in test_paths,
            )
        )
    for index, row in enumerate(fixture["inferred"]):
        session.add(
            GraphEdge(
                id=f"{repo_id}-inferred-{index}",
                repository_id=repo_id,
                source_node_id=row["test_file"],
                target_node_id=row["source_file"],
                edge_type="imports",
            )
        )
    session.add(
        GraphEdge(
            id=f"{repo_id}-structural",
            repository_id=repo_id,
            source_node_id=relationship["dependency"]["source"],
            target_node_id=relationship["dependency"]["target"],
            edge_type=relationship["dependency"]["type"],
        )
    )
    session.add(
        GraphEdge(
            id=f"{repo_id}-historical",
            repository_id=repo_id,
            source_node_id=relationship["co_change"]["source"],
            target_node_id=relationship["co_change"]["target"],
            edge_type=relationship["co_change"]["type"],
        )
    )
    await session.commit()


async def test_sealed_population_retains_basis_availability_exclusions_and_totals(
    async_session, tmp_path
):
    fixture = _fixture()
    repo = await insert_repo(async_session, name="alpha", head_commit="fixture-head")
    await _seed(async_session, repo.id, fixture)
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "exclude_patterns:\n  - src/excluded.py\n  - tests/excluded/**\n",
        encoding="utf-8",
    )

    impact = await analyze_test_impact(
        async_session,
        repo.id,
        fixture["changed_files"],
        repository_alias="alpha",
        exclude_spec=build_exclude_spec(tmp_path),
    )

    assert impact["files_total"] == 5
    assert impact["recommendations_total"] == len(impact["recommendations"]) == 16
    assert impact["recommendations_emitted"] == 16
    assert impact["recommendations_truncated"] is False
    assert impact["recommendations_omitted"] == 0
    assert impact["recommendations_by_primary_basis"] == {"measured": 14, "inferred": 2}
    assert {row["basis"] for row in impact["recommendations"]} == {"measured", "inferred"}
    assert all(
        row["repository"] == "alpha" and row["repository_id"] == repo.id
        for row in impact["recommendations"]
    )
    assert all(row["basis"] in row["bases"] for row in impact["recommendations"])
    assert all(
        row["evidence"] and all("basis" in item for item in row["evidence"])
        for row in impact["recommendations"]
    )
    assert all("excluded" not in row["test_id"] for row in impact["recommendations"])
    assert "src/excluded.py" not in {row["source_file"] for row in impact["files"]}

    both = next(row for row in impact["files"] if row["source_file"] == "src/both.py")
    assert both["measured_tests"] == ["tests/test_both.py::test_measured"]
    assert both["inferred_tests"] == ["tests/test_both_inferred.py"]
    assert "src/no_match.py" in impact["files_without_measured_tests"]
    assert "src/no_match.py" in impact["unknown_files"]
    assert impact["coverage"]["status"] == "partial"
    assert impact["coverage"]["freshness"]["status"] == "current"
    assert impact["analysis"]["partial"] is True


async def test_stale_coverage_is_explicit_even_when_it_has_matches(async_session):
    fixture = _fixture()
    repo = await insert_repo(async_session, name="stale", head_commit="fixture-head")
    await _seed(async_session, repo.id, fixture)
    repo.head_commit = "newer-index-head"
    await async_session.commit()

    impact = await analyze_test_impact(
        async_session,
        repo.id,
        ["src/measured.py"],
        repository_alias="stale",
    )

    assert impact["coverage"]["status"] == "available"
    assert impact["coverage"]["freshness"]["status"] == "stale"
    assert impact["analysis"]["stale"] is True
    assert impact["analysis"]["partial"] is True
    assert impact["recommendations"][0]["basis"] == "measured"


async def test_unavailable_coverage_is_not_an_empty_no_tests_claim(async_session):
    fixture = _fixture()
    repo = await insert_repo(async_session, name="unavailable")
    await _seed(async_session, repo.id, fixture, with_coverage=False)

    impact = await analyze_test_impact(
        async_session,
        repo.id,
        fixture["unavailable_changed_files"],
        repository_alias="unavailable",
    )

    assert impact["recommendations"] == []
    assert impact["recommendations_total"] == 0
    assert impact["recommendations_by_primary_basis"] == {"measured": 0, "inferred": 0}
    assert impact["coverage"]["status"] == "unavailable"
    assert impact["coverage"]["reason"] == "no_per_test_coverage_map"
    assert impact["unknown_files"] == fixture["unavailable_changed_files"]
    assert impact["analysis"]["status"] == "partial"


async def test_coverage_query_failure_is_explicitly_degraded(async_session, monkeypatch):
    fixture = _fixture()
    repo = await insert_repo(async_session, name="degraded")
    await _seed(async_session, repo.id, fixture, with_coverage=False)

    async def _boom(*args, **kwargs):
        raise RuntimeError("sealed coverage failure")

    monkeypatch.setattr(
        "repowise.core.persistence.crud.get_test_coverage_summary",
        _boom,
    )
    impact = await analyze_test_impact(
        async_session,
        repo.id,
        ["src/inferred.py"],
        repository_alias="degraded",
    )

    assert impact["coverage"]["status"] == "degraded"
    assert impact["analysis"]["status"] == "degraded"
    assert impact["analysis"]["partial"] is True
    assert impact["analysis"]["degraded"] is True
    assert impact["recommendations"][0]["basis"] == "inferred"


async def test_dedup_retains_measured_and_inferred_basis(async_session):
    repo = await insert_repo(async_session, name="dual-basis")
    await save_test_coverage(
        async_session,
        repo.id,
        [
            TestCoverage(
                test_id="tests/test_same.py",
                file_path="src/same.py",
                covered_lines=[1],
                source_format="coverage.py",
                test_file="tests/test_same.py",
            )
        ],
        source_format="coverage.py",
    )
    for index, (path, is_test) in enumerate((("src/same.py", False), ("tests/test_same.py", True))):
        async_session.add(
            GraphNode(
                id=f"dual-basis-node-{index}",
                repository_id=repo.id,
                node_id=path,
                node_type="file",
                is_test=is_test,
            )
        )
    async_session.add(
        GraphEdge(
            id="dual-basis-edge",
            repository_id=repo.id,
            source_node_id="tests/test_same.py",
            target_node_id="src/same.py",
            edge_type="imports",
        )
    )
    await async_session.commit()

    impact = await analyze_test_impact(async_session, repo.id, ["src/same.py"])

    assert impact["recommendations_total"] == 1
    assert impact["recommendations"][0]["basis"] == "measured"
    assert impact["recommendations"][0]["bases"] == ["measured", "inferred"]
    assert {row["basis"] for row in impact["recommendations"][0]["evidence"]} == {
        "measured",
        "inferred",
    }
    assert impact["analysis"]["basis_categories"] == ["measured", "inferred"]
    assert impact["files"][0]["inferred_tests_total"] == len(impact["files"][0]["inferred_tests"])


async def test_exclusion_uses_test_id_path_when_test_file_is_missing(async_session, tmp_path):
    repo = await insert_repo(async_session, name="excluded-node-id")
    await save_test_coverage(
        async_session,
        repo.id,
        [
            TestCoverage(
                test_id="tests/excluded/test_hidden.py::test_hidden",
                file_path="src/visible.py",
                covered_lines=[1],
                source_format="coverage.py",
                test_file=None,
            )
        ],
        source_format="coverage.py",
    )
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "exclude_patterns:\n  - tests/excluded/**\n",
        encoding="utf-8",
    )

    impact = await analyze_test_impact(
        async_session,
        repo.id,
        ["src/visible.py"],
        exclude_spec=build_exclude_spec(tmp_path),
    )

    assert impact["recommendations"] == []
    assert impact["recommendations_total"] == 0


async def test_same_named_tests_keep_repository_identity(async_session):
    fixture = _fixture()
    alpha = await insert_repo(async_session, name="alpha-same", local_path="/tmp/alpha")
    beta = await insert_repo(async_session, name="beta-same", local_path="/tmp/beta")
    row = fixture["same_named"]
    for repo in (alpha, beta):
        await save_test_coverage(
            async_session,
            repo.id,
            [
                TestCoverage(
                    test_id=row["test_id"],
                    file_path=row["source_file"],
                    covered_lines=[1],
                    source_format="coverage.py",
                    test_file="tests/test_shared.py",
                )
            ],
            source_format="coverage.py",
        )
    await async_session.commit()

    alpha_impact = await analyze_test_impact(
        async_session, alpha.id, [row["source_file"]], repository_alias="alpha"
    )
    beta_impact = await analyze_test_impact(
        async_session, beta.id, [row["source_file"]], repository_alias="beta"
    )

    assert alpha_impact["recommendations"][0]["test_id"] == row["test_id"]
    assert beta_impact["recommendations"][0]["test_id"] == row["test_id"]
    assert alpha_impact["recommendations"][0]["repository"] == "alpha"
    assert beta_impact["recommendations"][0]["repository"] == "beta"
    assert (
        alpha_impact["recommendations"][0]["repository_id"]
        != beta_impact["recommendations"][0]["repository_id"]
    )
