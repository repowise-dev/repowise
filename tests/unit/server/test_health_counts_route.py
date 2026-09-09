"""``counts=code_shape`` across the routes that answer to it.

The control exists because roughly half of a file's deduction is git-derived
and rises as the file is worked on, so the headline falls through a week of
good refactoring. Dropping that half is a subtraction over stored columns, not
a rescore — these fix what every surface does with it, because a headline that
moves while the map, the file list and the findings under it do not is the
contradiction the control was added to remove.
"""

from __future__ import annotations

from repowise.core.analysis.health.models import HealthFileMetricData
from repowise.core.persistence.crud import save_health_metrics, upsert_repository

from .conftest import create_test_repo


def _metric(path: str, score: float, structure: float | None, history: float | None, nloc: int = 100):
    return HealthFileMetricData(
        file_path=path,
        score=score,
        max_ccn=1,
        max_nesting=1,
        nloc=nloc,
        has_test_file=False,
        defect_score=score,
        structure_deduction=structure,
        history_deduction=history,
    )


async def _repo_with_metrics(client, session, tmp_path, metrics):
    repo = await create_test_repo(client, tmp_path)
    await upsert_repository(session, name="r", local_path=repo["local_path"])
    await save_health_metrics(session, repo["id"], metrics)
    await session.commit()
    return repo["id"]


async def test_the_headline_drops_the_history_half(client, session, tmp_path) -> None:
    """Two files whose scores are held down almost entirely by churn."""
    repo_id = await _repo_with_metrics(
        client,
        session,
        tmp_path,
        [_metric("a.py", 5.0, 1.0, 4.0), _metric("b.py", 6.0, 1.0, 3.0)],
    )

    full = (await client.get(f"/api/repos/{repo_id}/health/overview")).json()
    assert full["summary"]["average_health"] == 5.5
    assert full["summary"]["counts"] == "everything"

    shaped = (
        await client.get(f"/api/repos/{repo_id}/health/overview?counts=code_shape")
    ).json()
    assert shaped["summary"]["average_health"] == 9.0
    assert shaped["summary"]["counts"] == "code_shape"


async def test_the_distribution_is_recomputed_not_carried_over(client, session, tmp_path) -> None:
    """A band split describing the other reading is worse than none."""
    repo_id = await _repo_with_metrics(
        client, session, tmp_path, [_metric("a.py", 3.0, 0.5, 6.5)]
    )
    shaped = (
        await client.get(f"/api/repos/{repo_id}/health/overview?counts=code_shape")
    ).json()
    bands = shaped["distribution"]["bands"]
    assert bands["healthy"]["files"] == 1
    assert bands["alert"]["files"] == 0


async def test_rows_without_a_split_are_reported_not_scored_ten(client, session, tmp_path) -> None:
    repo_id = await _repo_with_metrics(
        client,
        session,
        tmp_path,
        [_metric("new.py", 5.0, 1.0, 4.0), _metric("old.py", 9.0, None, None)],
    )
    shaped = (
        await client.get(f"/api/repos/{repo_id}/health/overview?counts=code_shape")
    ).json()
    assert shaped["summary"]["unscored_files"] == 1
    assert shaped["summary"]["average_health"] == 9.0


async def test_the_file_list_is_re_ranked_by_the_same_reading(client, session, tmp_path) -> None:
    """Worst-first has to mean worst under the reading on screen."""
    repo_id = await _repo_with_metrics(
        client,
        session,
        tmp_path,
        [_metric("churned.py", 2.0, 0.5, 7.5), _metric("complex.py", 6.0, 4.0, 0.0)],
    )
    rows = (
        await client.get(f"/api/repos/{repo_id}/health/files?counts=code_shape&limit=5")
    ).json()
    items = rows.get("items") or rows.get("files")
    assert next(r["file_path"] for r in items) == "complex.py"
    assert items[0]["score"] == 6.0


async def test_an_unknown_value_is_refused_rather_than_guessed(client, session, tmp_path) -> None:
    """The query declares a pattern, so a typo 422s instead of silently
    serving the default reading under whatever label the caller sent."""
    repo_id = await _repo_with_metrics(
        client, session, tmp_path, [_metric("a.py", 5.0, 1.0, 4.0)]
    )
    resp = await client.get(f"/api/repos/{repo_id}/health/overview?counts=nonsense")
    assert resp.status_code == 422


async def test_the_stored_rows_are_never_rewritten(client, session, tmp_path) -> None:
    """The projection wraps live ORM rows; assigning to one would flush it."""
    repo_id = await _repo_with_metrics(
        client, session, tmp_path, [_metric("a.py", 5.0, 1.0, 4.0)]
    )
    await client.get(f"/api/repos/{repo_id}/health/overview?counts=code_shape")
    after = (await client.get(f"/api/repos/{repo_id}/health/overview")).json()
    assert after["summary"]["average_health"] == 5.0
