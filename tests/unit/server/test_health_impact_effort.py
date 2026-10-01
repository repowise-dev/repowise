"""The impact / effort plane route: the whole filtered set, never one page of it."""

from __future__ import annotations

from dataclasses import replace

from repowise.core.analysis.health.impact_effort import (
    EFFORT_MIDLINE_LINES,
    GAIN_MIDLINE_POINTS,
    PLOT_CAP,
)

from .test_health_work_queue_controls import _finding, _metric, _queue, _repo


async def _plane(client, repo_id, query: str = ""):
    url = f"/api/repos/{repo_id}/health/impact-effort"
    response = await client.get(f"{url}?{query}" if query else url)
    assert response.status_code == 200, response.text
    return response.json()


async def test_the_plane_holds_every_file_the_queue_counts(client, session, tmp_path) -> None:
    metrics = [_metric(f"f{i}.py", 4.0, nloc=50 + i) for i in range(7)]
    findings = [_finding(f"f{i}.py", impact=1.0 + i) for i in range(7)]
    repo_id = await _repo(client, session, tmp_path, metrics, findings)

    page = await _queue(client, repo_id, "limit=2")
    plane = await _plane(client, repo_id)

    assert len(page["targets"]) == 2
    assert plane["total"] == plane["plotted"] == page["total"] == 7
    assert plane["cap"] == PLOT_CAP
    assert plane["effort_midline_lines"] == EFFORT_MIDLINE_LINES
    assert plane["gain_midline_points"] == GAIN_MIDLINE_POINTS
    first = plane["points"][0]
    # No plan stored, so the file speaks for itself.
    assert first == {
        "file_path": "f6.py",
        "effort_lines": 56,
        "effort_basis": "file",
        "recoverable_health": 7.0,
        "tier": first["tier"],
    }


async def test_history_only_files_are_never_plotted(client, session, tmp_path) -> None:
    repo_id = await _repo(
        client,
        session,
        tmp_path,
        [_metric("a.py", 4.0), _metric("b.py", 5.0)],
        [_finding("a.py", "change_entropy"), _finding("b.py")],
    )

    plane = await _plane(client, repo_id)

    assert [p["file_path"] for p in plane["points"]] == ["b.py"]
    assert plane["history_only_excluded"] == 1


async def test_the_queue_filters_narrow_the_plane_too(client, session, tmp_path) -> None:
    repo_id = await _repo(
        client,
        session,
        tmp_path,
        [_metric("src/a.py", 4.0), _metric("lib/b.py", 5.0)],
        [_finding("src/a.py"), _finding("lib/b.py", severity="low")],
    )

    by_search = await _plane(client, repo_id, "search=src/")
    by_severity = await _plane(client, repo_id, "severity=low")

    assert [p["file_path"] for p in by_search["points"]] == ["src/a.py"]
    assert [p["file_path"] for p in by_severity["points"]] == ["lib/b.py"]


async def test_a_queue_row_says_whether_it_is_a_test(client, session, tmp_path) -> None:
    repo_id = await _repo(
        client,
        session,
        tmp_path,
        [_metric("a.py", 4.0), replace(_metric("tests/test_a.py", 5.0), is_test=True)],
        [_finding("a.py"), _finding("tests/test_a.py")],
    )

    rows = {t["file_path"]: t["is_test"] for t in (await _queue(client, repo_id))["targets"]}
    production = await _queue(client, repo_id, "scope=production")

    assert rows == {"a.py": False, "tests/test_a.py": True}
    assert [t["file_path"] for t in production["targets"]] == ["a.py"]


async def test_an_unknown_repository_is_a_404(client) -> None:
    response = await client.get("/api/repos/nope/health/impact-effort")
    assert response.status_code == 404
