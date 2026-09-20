"""REST blast-radius exposes the canonical test-impact population unchanged."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.analysis.health.coverage import TestCoverage
from repowise.core.analysis.pr_blast import PRBlastRadiusAnalyzer
from repowise.core.persistence.crud import save_test_coverage
from repowise.core.persistence.models import GraphEdge, GraphNode
from tests.unit.server.conftest import create_test_repo

_FIXTURE = Path(__file__).parents[2] / "fixtures" / "mcp" / "pr_test_impact.json"


async def test_rest_and_shared_analyzer_return_identical_test_impact(client, session, tmp_path):
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    repo = await create_test_repo(client, tmp_path)
    repo_id = repo["id"]
    changed = fixture["changed_files"]
    config_dir = Path(repo["local_path"]) / ".repowise"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "exclude_patterns:\n  - src/excluded.py\n  - tests/excluded/**\n",
        encoding="utf-8",
    )

    paths = set(changed)
    paths.update(row["source_file"] for row in fixture["coverage"])
    paths.update(row["test_file"] for row in fixture["coverage"])
    paths.update(row["source_file"] for row in fixture["inferred"])
    paths.update(row["test_file"] for row in fixture["inferred"])
    test_paths = {path for path in paths if path.startswith("tests/")}
    for index, path in enumerate(sorted(paths)):
        session.add(
            GraphNode(
                id=f"rest-impact-node-{index}",
                repository_id=repo_id,
                node_id=path,
                node_type="file",
                is_test=path in test_paths,
            )
        )
    for index, row in enumerate(fixture["inferred"]):
        session.add(
            GraphEdge(
                id=f"rest-impact-edge-{index}",
                repository_id=repo_id,
                source_node_id=row["test_file"],
                target_node_id=row["source_file"],
                edge_type="imports",
            )
        )
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
    )
    await session.commit()

    internal = await PRBlastRadiusAnalyzer(session, repo_id).analyze_files(changed)
    response = await client.post(
        f"/api/repos/{repo_id}/blast-radius",
        json={"changed_files": changed, "max_depth": 3},
    )

    assert response.status_code == 200
    external = response.json()
    assert external["test_impact"] == internal["test_impact"]
    assert external["structural_impact_score"] == external["overall_risk_score"]
    assert external["structural_impact_band"] in {"localized", "moderate", "broad"}
    assert external["structural_impact_scale"]["unit"] == "normalized_points"
    assert external["structural_impact_scale"]["calibration"]["status"] == "uncalibrated"
    assert external["structural_impact_scale"]["runtime_breakage_probability"] is False
    assert external["overall_risk_score_compatibility"]["replacement"] == (
        "structural_impact_score"
    )
    assert all(row["structural_score"] == row["risk_score"] for row in external["direct_risks"])
    assert external["test_impact"]["recommendations_total"] == 16
    assert external["test_impact"]["recommendations_emitted"] == len(
        external["test_impact"]["recommendations"]
    )
    assert "src/excluded.py" not in {row["source_file"] for row in external["test_impact"]["files"]}
    assert all(
        "excluded" not in row["test_id"] for row in external["test_impact"]["recommendations"]
    )
    assert all("basis" in row for row in external["test_impact"]["recommendations"])


async def test_openapi_exposes_typed_test_impact_contract(client):
    schema = (await client.get("/openapi.json")).json()["components"]["schemas"]

    blast = schema["BlastRadiusResponse"]
    assert blast["properties"]["test_impact"]["$ref"].endswith("/TestImpactResponse")
    assert blast["properties"]["structural_impact_scale"]["$ref"].endswith("/RiskScalarSemantics")
    assert "structural_impact_score" in blast["required"]
    assert "overall_risk_score_compatibility" in blast["required"]
    recommendation = schema["TestRecommendation"]
    assert recommendation["required"] == [
        "test_id",
        "repository_id",
        "repository",
        "basis",
        "bases",
        "source_files",
        "evidence",
    ]
    assert recommendation["properties"]["basis"]["enum"] == ["measured", "inferred"]
