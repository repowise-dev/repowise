"""Finding status updates stay within the repository in the URL."""

from __future__ import annotations

import pytest

from repowise.core.persistence.models import HealthFinding, Repository


@pytest.mark.parametrize("repo_id", ["other", "missing"])
async def test_status_update_refuses_another_repository(client, session_factory, tmp_path, repo_id):
    async with session_factory() as session:
        session.add_all(
            [
                Repository(id=name, name=name, local_path=str(tmp_path / name))
                for name in ("owner", "other")
            ]
        )
        await session.flush()
        finding = HealthFinding(
            repository_id="owner",
            file_path="a.py",
            biomarker_type="complex_method",
            severity="high",
            health_impact=2.5,
        )
        session.add(finding)
        await session.commit()
        finding_id = finding.id

    response = await client.patch(
        f"/api/repos/{repo_id}/health/findings/{finding_id}", json={"status": "resolved"}
    )
    assert response.status_code == 404
    async with session_factory() as session:
        finding = await session.get(HealthFinding, finding_id)
        assert finding.status == "open"
