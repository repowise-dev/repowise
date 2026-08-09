"""``/health/trend``'s per-file movement block.

The block is headed "Largest score changes since last index" and was built by
sorting the deltas ascending and slicing the first 50 — that is, the 50
biggest *drops*. Improvements only appeared while fewer than 50 files had
regressed, so the one index that most wants to show progress, the one after a
cleanup or a re-score, is the one that hides it. And with no total beside the
slice a reader could not tell a complete list from a truncated one.

Measured on the repowise index when this was written: only 15 files moved
between the last two snapshots, so the cap was latent — but the oldest-vs-newest
comparison, which is the shape a full re-index produces, moves 183 files with
107 regressions. These construct that state rather than waiting for it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from repowise.core.persistence.crud import save_health_snapshot, upsert_repository
from repowise.server.routers.code_health.trends_routes import FILE_DELTA_LIMIT

from .conftest import create_test_repo


async def _two_snapshots(session, repo_id: str, before: dict, after: dict) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for day, scores in ((0, before), (1, after)):
        await save_health_snapshot(
            session,
            repo_id,
            hotspot_health=5.0,
            average_health=5.0,
            worst_performer_path="a.py",
            worst_performer_score=1.0,
            per_file_scores=scores,
            taken_at=base + timedelta(days=day),
        )
    await session.commit()


async def _repo(client, session, tmp_path):
    repo = await create_test_repo(client, tmp_path)
    await upsert_repository(session, name="r", local_path=repo["local_path"])
    return repo["id"]


async def test_improvements_survive_a_wall_of_regressions(client, session, tmp_path) -> None:
    """The failure the ascending sort produced, constructed exactly.

    ``FILE_DELTA_LIMIT`` + 10 files regress by a little and three improve by a
    lot. Sorted ascending, the improvements sit past the end of the slice and
    never reach the page, even though they are the largest changes in the repo.
    """
    repo_id = await _repo(client, session, tmp_path)
    before = {f"r{i}.py": 8.0 for i in range(FILE_DELTA_LIMIT + 10)}
    after = {f"r{i}.py": 7.9 for i in range(FILE_DELTA_LIMIT + 10)}
    for i in range(3):
        before[f"win{i}.py"] = 2.0
        after[f"win{i}.py"] = 9.0

    await _two_snapshots(session, repo_id, before, after)
    resp = await client.get(f"/api/repos/{repo_id}/health/trend")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    paths = [d["file_path"] for d in body["file_deltas"]]
    assert [p for p in paths if p.startswith("win")] == ["win0.py", "win1.py", "win2.py"]
    # And they lead, because they are the biggest movements in either direction.
    assert paths[:3] == ["win0.py", "win1.py", "win2.py"]


async def test_the_total_counts_everything_the_slice_dropped(client, session, tmp_path) -> None:
    repo_id = await _repo(client, session, tmp_path)
    n = FILE_DELTA_LIMIT + 27
    before = {f"f{i}.py": 8.0 for i in range(n)}
    # Distinct magnitudes so the ordering is total and the slice is well-defined.
    after = {f"f{i}.py": 8.0 - (i + 1) / 100 for i in range(n)}
    await _two_snapshots(session, repo_id, before, after)

    body = (await client.get(f"/api/repos/{repo_id}/health/trend")).json()

    assert len(body["file_deltas"]) == FILE_DELTA_LIMIT
    assert body["file_deltas_total"] == n


async def test_the_total_equals_the_list_when_nothing_is_dropped(
    client, session, tmp_path
) -> None:
    """The common case, and the one that makes the UI say "all N" rather than
    "the N largest of N"."""
    repo_id = await _repo(client, session, tmp_path)
    await _two_snapshots(
        session, repo_id, {"a.py": 8.0, "b.py": 5.0}, {"a.py": 7.0, "b.py": 6.0}
    )

    body = (await client.get(f"/api/repos/{repo_id}/health/trend")).json()

    assert body["file_deltas_total"] == 2 == len(body["file_deltas"])


async def test_unchanged_files_are_not_counted_as_movement(client, session, tmp_path) -> None:
    """``file_deltas_total`` counts files that moved, not files that exist —
    otherwise "3 of 3,263" would read as though the repo barely changed.

    A guard, not a regression test: the zero-delta filter predates this change,
    so only the ``file_deltas_total`` lookup fails on revert.
    """
    repo_id = await _repo(client, session, tmp_path)
    before = {f"f{i}.py": 8.0 for i in range(20)}
    after = dict(before)
    after["f0.py"] = 6.0

    await _two_snapshots(session, repo_id, before, after)
    body = (await client.get(f"/api/repos/{repo_id}/health/trend")).json()

    assert body["file_deltas_total"] == 1


async def test_ordering_is_total_across_equal_magnitudes(client, session, tmp_path) -> None:
    """Ties broken by path, so the slice boundary cannot shuffle between
    requests — the set membership of a truncated list has to be stable."""
    repo_id = await _repo(client, session, tmp_path)
    before = {"c.py": 8.0, "a.py": 8.0, "b.py": 8.0}
    # Same magnitude, opposite directions: a |delta| sort alone is ambiguous.
    after = {"c.py": 7.0, "a.py": 9.0, "b.py": 7.0}
    await _two_snapshots(session, repo_id, before, after)

    body = (await client.get(f"/api/repos/{repo_id}/health/trend")).json()

    assert [d["file_path"] for d in body["file_deltas"]] == ["a.py", "b.py", "c.py"]


async def test_a_single_snapshot_reports_no_movement(client, session, tmp_path) -> None:
    """Zero-history edge: the block is empty and the total says so rather than
    being absent, so the UI never has to guess whether the field is missing or
    the answer is nothing."""
    repo_id = await _repo(client, session, tmp_path)
    await save_health_snapshot(
        session,
        repo_id,
        hotspot_health=5.0,
        average_health=5.0,
        worst_performer_path=None,
        worst_performer_score=None,
        per_file_scores={"a.py": 5.0},
    )
    await session.commit()

    body = (await client.get(f"/api/repos/{repo_id}/health/trend")).json()

    assert body["file_deltas"] == []
    assert body["file_deltas_total"] == 0
    assert body["snapshot_count"] == 1
