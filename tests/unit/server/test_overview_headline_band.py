"""The REST overview grades the headline only when enough code was analysed."""

from __future__ import annotations

from repowise.core.analysis.health.models import HealthFileMetricData
from repowise.core.persistence.crud import save_health_metrics, upsert_repository

from .conftest import create_test_repo


def _metric(path: str, score: float | None) -> HealthFileMetricData:
    return HealthFileMetricData(
        file_path=path,
        score=score,
        max_ccn=None if score is None else 1,
        max_nesting=None if score is None else 1,
        nloc=10,
        has_test_file=False,
        defect_score=score,
    )


async def _repo(client, session, tmp_path, metrics) -> str:
    repo = await create_test_repo(client, tmp_path)
    await upsert_repository(session, name="r", local_path=repo["local_path"])
    await save_health_metrics(session, repo["id"], metrics)
    await session.commit()
    return repo["id"]


async def test_a_mostly_unanalysed_repo_has_no_band(client, session, tmp_path) -> None:
    metrics = [_metric("a.swift", 10.0)] + [_metric(f"u{i}.swift", None) for i in range(3)]
    repo_id = await _repo(client, session, tmp_path, metrics)

    health = (await client.get(f"/api/repos/{repo_id}/overview-summary")).json()["health"]
    assert health["average_health"] == 10.0
    assert health["unanalysed_file_count"] == 3
    assert health["analysed_share"] == 0.25
    assert health["band"] is None

    summary = (await client.get(f"/api/repos/{repo_id}/health/overview")).json()["summary"]
    assert summary["analysed_share"] == 0.25
    assert summary["band"] is None


async def test_a_fully_analysed_repo_keeps_its_band(client, session, tmp_path) -> None:
    repo_id = await _repo(client, session, tmp_path, [_metric("a.py", 9.0), _metric("b.py", 9.0)])

    health = (await client.get(f"/api/repos/{repo_id}/overview-summary")).json()["health"]
    assert health["analysed_share"] == 1.0
    assert health["band"] == "excellent"
