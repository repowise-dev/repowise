from __future__ import annotations

from repowise.core.persistence.crud import (
    get_health_findings,
    get_health_metrics,
    get_health_summary,
    save_health_findings,
    save_health_metrics,
    upsert_repository,
)
from repowise.core.pipeline.persist import _prune_stale_file_rows


async def test_health_reads_honor_repo_settings_excludes(session, tmp_path) -> None:
    repo = await upsert_repository(
        session,
        name="repo",
        local_path=str(tmp_path),
        settings={"exclude_patterns": ["tools/"]},
    )

    await save_health_metrics(
        session,
        repo.id,
        [
            {
                "file_path": "tools/gen.py",
                "score": 1.5,
                "max_ccn": 8,
                "max_nesting": 3,
                "nloc": 80,
                "duplication_pct": 0.0,
                "has_test_file": False,
                "line_coverage_pct": 0.0,
                "branch_coverage_pct": 0.0,
                "module": "tools",
            },
            {
                "file_path": "src/app.py",
                "score": 8.5,
                "max_ccn": 2,
                "max_nesting": 1,
                "nloc": 40,
                "duplication_pct": 0.0,
                "has_test_file": True,
                "line_coverage_pct": 90.0,
                "branch_coverage_pct": 80.0,
                "module": "src",
            },
        ],
    )
    await save_health_findings(
        session,
        repo.id,
        [
            {
                "file_path": "tools/gen.py",
                "biomarker_type": "complex_method",
                "severity": "high",
                "function_name": "build",
                "line_start": 1,
                "line_end": 20,
                "details": {},
                "health_impact": 2.5,
                "reason": "generated file finding",
            },
            {
                "file_path": "src/app.py",
                "biomarker_type": "complex_method",
                "severity": "low",
                "function_name": "run",
                "line_start": 1,
                "line_end": 10,
                "details": {},
                "health_impact": 0.5,
                "reason": "real file finding",
            },
        ],
    )

    metrics = await get_health_metrics(session, repo.id)
    findings = await get_health_findings(session, repo.id)
    summary = await get_health_summary(session, repo.id)

    assert [m.file_path for m in metrics] == ["src/app.py"]
    assert [f.file_path for f in findings] == ["src/app.py"]
    assert await get_health_metrics(session, repo.id, file_paths=["tools/gen.py"]) == []
    assert await get_health_findings(session, repo.id, file_path="tools/gen.py") == []
    assert summary == {
        "file_count": 1,
        "average_health": 8.5,
        "worst_performer_path": "src/app.py",
        "worst_performer_score": 8.5,
        "open_findings": 1,
        "maintainability_average": None,
        "performance_average": None,
        "maintainability_findings": 0,
        "performance_findings": 0,
        "performance_findings_density": None,
        "performance_coverage_pct": None,
        "performance_covered_files": 0,
        "performance_analyzed_files": 0,
        "performance_skipped_files": 0,
        "performance_unsupported_languages": [],
        "worst_performance_path": None,
        "worst_performance_score": None,
    }


async def test_prune_stale_rows_removes_excluded_health_data(session, tmp_path) -> None:
    repo = await upsert_repository(
        session,
        name="repo",
        local_path=str(tmp_path),
    )

    await save_health_metrics(
        session,
        repo.id,
        [
            {
                "file_path": "tools/gen.py",
                "score": 1.5,
                "max_ccn": 8,
                "max_nesting": 3,
                "nloc": 80,
                "duplication_pct": 0.0,
                "has_test_file": False,
                "line_coverage_pct": 0.0,
                "branch_coverage_pct": 0.0,
                "module": "tools",
            },
            {
                "file_path": "src/app.py",
                "score": 8.5,
                "max_ccn": 2,
                "max_nesting": 1,
                "nloc": 40,
                "duplication_pct": 0.0,
                "has_test_file": True,
                "line_coverage_pct": 90.0,
                "branch_coverage_pct": 80.0,
                "module": "src",
            },
        ],
    )
    await save_health_findings(
        session,
        repo.id,
        [
            {
                "file_path": "tools/gen.py",
                "biomarker_type": "complex_method",
                "severity": "high",
                "function_name": "build",
                "line_start": 1,
                "line_end": 20,
                "details": {},
                "health_impact": 2.5,
                "reason": "generated file finding",
            },
            {
                "file_path": "src/app.py",
                "biomarker_type": "complex_method",
                "severity": "low",
                "function_name": "run",
                "line_start": 1,
                "line_end": 10,
                "details": {},
                "health_impact": 0.5,
                "reason": "real file finding",
            },
        ],
    )

    await _prune_stale_file_rows(session, repo.id, {"src/app.py"}, set())

    metrics = await get_health_metrics(session, repo.id)
    findings = await get_health_findings(session, repo.id)

    assert [m.file_path for m in metrics] == ["src/app.py"]
    assert [f.file_path for f in findings] == ["src/app.py"]
