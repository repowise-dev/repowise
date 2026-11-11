"""One performance headline, read the same way by every surface.

The default queue leads, every production cause and every cause give it
context, and the summary, the editor files and the CLI all print that one
count rather than each summing raw findings over every context.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import update

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.opportunities import link_performance_findings
from repowise.core.generation.editor_files.fetcher import EditorFileDataFetcher
from repowise.core.persistence.crud import (
    finalize_performance_opportunities,
    get_health_summary,
    performance_headline,
    save_health_findings,
    save_health_metrics,
    upsert_repository,
)
from repowise.core.persistence.models import PerformanceOpportunity


def _metric(path: str) -> dict:
    return {
        "file_path": path,
        "score": 8.0,
        "max_ccn": 1,
        "max_nesting": 1,
        "nloc": 100,
        "has_test_file": False,
        "module": "src",
        "performance_score": 9.5,
    }


def _perf_finding(path: str) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type="io_in_loop",
        severity=Severity.MEDIUM,
        file_path=path,
        function_name="run",
        line_start=1,
        line_end=1,
        details={
            "boundary_kind": "db",
            "cross_function": True,
            "path": [f"{path}::run", "src/db.py::fetch"],
            "resolution_basis": "reliable-edge",
        },
        health_impact=0.0,
        reason="Database work repeats for every loop iteration.",
        dimension="performance",
    )


# (file, execution context, in the default queue)
_CAUSES = (
    ("src/queued.py", "production", True),
    ("src/waiting.py", "production", False),
    ("tests/test_loop.py", "test", False),
)


async def _seed(async_session, tmp_path):
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await save_health_metrics(async_session, repo.id, [_metric(p) for p, _, _ in _CAUSES])
    findings = [_perf_finding(p) for p, _, _ in _CAUSES]
    link_performance_findings(findings)
    await save_health_findings(async_session, repo.id, findings)
    await finalize_performance_opportunities(async_session, repo.id, analyzed_commit="a" * 40)
    # The verdicts the finalizer stores, set directly so each scope holds a known row.
    for path, context, queued in _CAUSES:
        await async_session.execute(
            update(PerformanceOpportunity)
            .where(
                PerformanceOpportunity.repository_id == repo.id,
                PerformanceOpportunity.file_path == path,
            )
            .values(execution_context=context, queue_eligible=queued)
        )
    await async_session.flush()
    return repo


async def test_the_headline_leads_with_the_queue_and_keeps_the_rest_as_context(
    async_session, tmp_path
) -> None:
    repo = await _seed(async_session, tmp_path)
    causes = await performance_headline(async_session, repo.id, covered_nloc=30_000)
    assert (causes.now, causes.production, causes.all) == (1, 2, 3)
    # Density is every cause over the covered lines, computed once here.
    assert causes.per_10k_loc == 1.0
    assert (await performance_headline(async_session, repo.id, covered_nloc=0)).per_10k_loc is None


async def test_the_summary_and_the_editor_files_print_the_same_headline(
    async_session, tmp_path
) -> None:
    repo = await _seed(async_session, tmp_path)
    summary = await get_health_summary(async_session, repo.id)
    block = await EditorFileDataFetcher(async_session, repo.id, Path(tmp_path))._get_code_health()
    assert block is not None
    assert summary["performance_causes"] == block.performance_causes.as_dict()
    assert (block.performance_causes.now, block.performance_causes.all) == (1, 3)
    # Raw finding counts are gone from the summary, so no surface can lead with them.
    assert "performance_findings" not in summary
