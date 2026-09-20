"""Exact external PR payload over the sealed test-impact fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_FIXTURE = Path(__file__).parents[3] / "fixtures" / "mcp" / "pr_test_impact.json"


@pytest.mark.asyncio
async def test_sealed_pr_payload_is_directive_first_typed_and_count_exact(setup_mcp, session):
    from repowise.core.analysis.health.coverage import TestCoverage
    from repowise.core.persistence.crud import save_test_coverage
    from repowise.core.persistence.models import GraphEdge, GraphNode
    from repowise.server.mcp_server import get_risk

    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    changed = [path for path in fixture["changed_files"] if path != "src/excluded.py"]
    paths = set(changed)
    paths.update(row["source_file"] for row in fixture["coverage"])
    paths.update(row["test_file"] for row in fixture["coverage"])
    paths.update(
        row["source_file"] for row in fixture["inferred"] if "excluded" not in row["test_file"]
    )
    paths.update(
        row["test_file"] for row in fixture["inferred"] if "excluded" not in row["test_file"]
    )
    relation = fixture["relationship_semantics"]
    paths.update([relation["dependency"]["source"], relation["co_change"]["source"]])
    test_paths = {path for path in paths if path.startswith("tests/")}
    for index, path in enumerate(sorted(paths)):
        session.add(
            GraphNode(
                id=f"sealed-impact-node-{index}",
                repository_id=setup_mcp,
                node_id=path,
                node_type="file",
                is_test=path in test_paths,
            )
        )
    for index, row in enumerate(
        item for item in fixture["inferred"] if "excluded" not in item["test_file"]
    ):
        session.add(
            GraphEdge(
                id=f"sealed-impact-inferred-{index}",
                repository_id=setup_mcp,
                source_node_id=row["test_file"],
                target_node_id=row["source_file"],
                edge_type="imports",
            )
        )
    session.add(
        GraphEdge(
            id="sealed-impact-structural",
            repository_id=setup_mcp,
            source_node_id=relation["dependency"]["source"],
            target_node_id=relation["dependency"]["target"],
            edge_type=relation["dependency"]["type"],
        )
    )
    session.add(
        GraphEdge(
            id="sealed-impact-historical",
            repository_id=setup_mcp,
            source_node_id=relation["co_change"]["source"],
            target_node_id=relation["co_change"]["target"],
            edge_type=relation["co_change"]["type"],
        )
    )
    await save_test_coverage(
        session,
        setup_mcp,
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
    await session.flush()

    payload = await get_risk(["src/measured.py"], changed_files=changed, include=["graph"])
    external = json.loads(json.dumps(payload))
    directive = external["directive"]
    full = external["pr_blast_radius"]["test_impact"]

    assert next(iter(external)) == "directive"
    assert full["recommendations_total"] == len(full["recommendations"]) == 16
    assert full["recommendations_by_primary_basis"] == {"measured": 14, "inferred": 2}
    assert full["recommendations_truncated"] is False
    assert directive["test_recommendations_total"] == 16
    assert directive["test_recommendations_emitted"] == len(directive["test_recommendations"]) == 10
    assert directive["test_recommendations_truncated"] is True
    assert directive["test_recommendations_omitted"] == 6
    assert directive["tests_to_run"] == [
        row["test_id"] for row in directive["test_recommendations"]
    ]
    assert directive["tests_to_run_total"] == 14
    assert directive["tests_to_run_basis"] == "measured"
    assert directive["missing_tests"] == ["src/no_match.py"]
    assert directive["missing_tests_total"] == directive["missing_tests_emitted"] == 1
    assert directive["missing_tests_truncated"] is False
    assert directive["missing_tests_omitted"] == 0
    assert directive["files_without_measured_tests"] == ["src/inferred.py", "src/no_match.py"]
    assert all(
        row["basis"] in {"measured", "inferred"} for row in directive["test_recommendations"]
    )
    assert all(row["evidence"] for row in directive["test_recommendations"])
    assert all(row["repository_id"] == setup_mcp for row in full["recommendations"])
    assert relation["dependency"]["source"] in directive["may_break"]
    assert relation["co_change"]["source"] not in directive["may_break"]
    assert external["_meta"]["contract_version"]
