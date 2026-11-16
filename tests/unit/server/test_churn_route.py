from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from repowise.server.routers.code_health import churn_routes


@pytest.mark.asyncio
async def test_quiet_window_reports_newest_change_to_a_scored_file(monkeypatch) -> None:
    async def repo(_session, _repo_id):
        return SimpleNamespace(id="r")

    async def metrics(_session, _repo_id):
        return [
            SimpleNamespace(file_path="a.tcl", score=7.0, max_ccn=9, nloc=40, scored=True),
            SimpleNamespace(file_path="unscored.ini", score=None, max_ccn=0, nloc=0, scored=False),
        ]

    async def git_meta(_session, _repo_id):
        return {
            "a.tcl": SimpleNamespace(
                commit_count_90d=0, churn_percentile=0.8, last_commit_at=datetime(2026, 7, 1, tzinfo=UTC)
            ),
            "unscored.ini": SimpleNamespace(
                commit_count_90d=3, churn_percentile=1.0, last_commit_at=datetime(2026, 10, 8, tzinfo=UTC)
            ),
        }

    monkeypatch.setattr(churn_routes.crud, "get_repository", repo)
    monkeypatch.setattr(churn_routes.crud, "get_health_metrics", metrics)
    monkeypatch.setattr(churn_routes.crud, "get_all_git_metadata", git_meta)

    payload = await churn_routes.churn_complexity("r", limit=300, session=None)

    assert payload["total"] == 0
    assert payload["latest_change_at"].startswith("2026-07-01")
