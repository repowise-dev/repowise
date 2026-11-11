"""The REST health summaries name the worst file in Fix first's scope.

A script or generated file scoring lowest is not the file to work on, so both
REST payloads name the production file ``get_health`` lists first.
"""

from __future__ import annotations

from repowise.core.analysis.health.models import HealthFileMetricData
from repowise.core.persistence.crud import save_health_metrics, upsert_repository

from .conftest import create_test_repo


def _metric(path: str, score: float, origin: str | None = None) -> HealthFileMetricData:
    return HealthFileMetricData(
        file_path=path,
        score=score,
        max_ccn=1,
        max_nesting=1,
        nloc=100,
        has_test_file=False,
        defect_score=score,
        code_origin=origin,
    )


async def test_rest_summaries_skip_out_of_scope_files(client, session, tmp_path) -> None:
    repo = await create_test_repo(client, tmp_path)
    await upsert_repository(session, name="r", local_path=repo["local_path"])
    await save_health_metrics(
        session,
        repo["id"],
        [
            _metric("scripts/migrate.py", 1.0, "tooling"),
            _metric("src/proto/schema_pb2.py", 1.5, "generated"),
            _metric("src/app/service.py", 4.0),
            _metric("src/app/models.py", 8.0),
        ],
    )
    await session.commit()

    health = (await client.get(f"/api/repos/{repo['id']}/health/overview")).json()
    overview = (await client.get(f"/api/repos/{repo['id']}/overview-summary")).json()
    assert health["summary"]["worst_performer_path"] == "src/app/service.py"
    assert overview["health"]["worst_performer_path"] == "src/app/service.py"
