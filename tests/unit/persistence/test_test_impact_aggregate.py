"""analyze_test_impact with stored line coverage and no per-test map."""

from __future__ import annotations


async def test_analyze_names_stored_line_coverage_when_there_is_no_per_test_map(
    async_session,
):
    from repowise.core.analysis.test_impact import analyze_test_impact
    from repowise.core.persistence.crud import save_coverage_files
    from tests.unit.persistence.helpers import insert_repo

    repo = await insert_repo(async_session, name="aggregate", head_commit="head")
    await save_coverage_files(
        async_session,
        repo.id,
        [
            {
                "file_path": "src/a.py",
                "line_coverage_pct": 50.0,
                "covered_lines": [1],
                "total_coverable_lines": 2,
                "branch_coverage_pct": None,
            }
        ],
        source_format="lcov",
        ingested_commit_sha="head",
    )

    impact = await analyze_test_impact(async_session, repo.id, ["src/a.py"])

    assert impact["coverage"]["reason"] == "no_per_test_map_aggregate_available"
    assert impact["coverage"]["aggregate"] == {
        "file_count": 1,
        "ingested_commit": "head",
        "freshness": "current",
    }
