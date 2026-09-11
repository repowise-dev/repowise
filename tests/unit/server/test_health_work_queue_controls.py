"""The work queue's own filters, paging and counts.

The queue is the triage list: one view, files ranked by leverage, narrowed by
controls that must describe the rows beside them. These cover the controls the
route grew for that view, and the two figures a reader steers by - how many
files match, and how much work sits in them.
"""

from __future__ import annotations

from repowise.core.analysis.health.models import HealthFindingData, HealthFileMetricData
from repowise.core.persistence.crud import (
    save_health_findings,
    save_health_metrics,
    upsert_repository,
)

from .conftest import create_test_repo


def _metric(path: str, score: float, nloc: int = 100, has_test_file: bool = False):
    return HealthFileMetricData(
        file_path=path,
        score=score,
        max_ccn=1,
        max_nesting=1,
        nloc=nloc,
        has_test_file=has_test_file,
        defect_score=score,
        structure_deduction=10.0 - score,
        history_deduction=0.0,
    )


def _finding(
    path: str,
    biomarker_type: str = "complex_method",
    severity: str = "high",
    impact: float = 1.0,
    dimension: str = "defect",
):
    return HealthFindingData(
        biomarker_type=biomarker_type,
        severity=severity,
        file_path=path,
        function_name=None,
        line_start=12,
        line_end=20,
        details={},
        health_impact=impact,
        reason=f"{biomarker_type} in {path}",
        dimension=dimension,
    )


async def _repo(client, session, tmp_path, metrics, findings):
    repo = await create_test_repo(client, tmp_path)
    await upsert_repository(session, name="r", local_path=repo["local_path"])
    await save_health_metrics(session, repo["id"], metrics)
    await save_health_findings(session, repo["id"], findings)
    await session.commit()
    return repo["id"]


async def _queue(client, repo_id, query: str = ""):
    url = f"/api/repos/{repo_id}/health/refactoring-targets"
    return (await client.get(f"{url}?{query}" if query else url)).json()


async def test_the_page_reports_both_files_and_findings(client, session, tmp_path) -> None:
    """The view lists files but triages findings, so it states both."""
    repo_id = await _repo(
        client,
        session,
        tmp_path,
        [_metric("a.py", 4.0), _metric("b.py", 5.0)],
        [_finding("a.py"), _finding("a.py", "god_class"), _finding("b.py")],
    )

    body = await _queue(client, repo_id)

    assert body["total"] == 2
    assert body["finding_total"] == 3
    assert body["offset"] == 0


async def test_paging_walks_the_whole_ranking(client, session, tmp_path) -> None:
    """`total` counts the matches; `offset` moves through them without gaps."""
    metrics = [_metric(f"f{i}.py", 3.0 + i * 0.1) for i in range(5)]
    findings = [_finding(f"f{i}.py", impact=5.0 - i) for i in range(5)]
    repo_id = await _repo(client, session, tmp_path, metrics, findings)

    first = await _queue(client, repo_id, "limit=2&offset=0")
    second = await _queue(client, repo_id, "limit=2&offset=2")
    last = await _queue(client, repo_id, "limit=2&offset=4")

    assert [t["file_path"] for t in first["targets"]] == ["f0.py", "f1.py"]
    assert [t["file_path"] for t in second["targets"]] == ["f2.py", "f3.py"]
    assert [t["file_path"] for t in last["targets"]] == ["f4.py"]
    # Every page describes the same population.
    assert first["total"] == second["total"] == last["total"] == 5


async def test_an_offset_past_the_end_is_an_empty_page_not_an_error(
    client, session, tmp_path
) -> None:
    repo_id = await _repo(client, session, tmp_path, [_metric("a.py", 4.0)], [_finding("a.py")])

    body = await _queue(client, repo_id, "offset=500")

    assert body["targets"] == []
    assert body["total"] == 1


async def test_severity_selects_exactly_what_was_asked_for(client, session, tmp_path) -> None:
    repo_id = await _repo(
        client,
        session,
        tmp_path,
        [_metric("a.py", 4.0), _metric("b.py", 5.0)],
        [_finding("a.py", severity="low"), _finding("b.py", severity="critical")],
    )

    only_low = await _queue(client, repo_id, "severity=low")

    assert [t["file_path"] for t in only_low["targets"]] == ["a.py"]


async def test_an_empty_severity_list_does_not_empty_the_queue(
    client, session, tmp_path
) -> None:
    """A client that serializes "nothing selected" as "," means no filter.

    Read as an exact match, an empty set matches nothing and returns 200 with
    an empty list - a filter that looks like a repository with no findings.
    """
    repo_id = await _repo(
        client,
        session,
        tmp_path,
        [_metric("a.py", 4.0)],
        [_finding("a.py", severity="low")],
    )

    assert (await _queue(client, repo_id, "severity=,"))["total"] == 1
    assert (await _queue(client, repo_id, "severity=%20"))["total"] == 1


async def test_search_and_the_file_chips_narrow_the_same_rows(
    client, session, tmp_path
) -> None:
    repo_id = await _repo(
        client,
        session,
        tmp_path,
        [_metric("src/a.py", 4.0), _metric("web/b.py", 9.0, has_test_file=True)],
        [_finding("src/a.py"), _finding("web/b.py")],
    )

    assert [t["file_path"] for t in (await _queue(client, repo_id, "search=web"))["targets"]] == [
        "web/b.py"
    ]
    # Below the score green starts at.
    assert [
        t["file_path"] for t in (await _queue(client, repo_id, "only_failing=true"))["targets"]
    ] == ["src/a.py"]
    assert [
        t["file_path"] for t in (await _queue(client, repo_id, "only_untested=true"))["targets"]
    ] == ["src/a.py"]


async def test_performance_is_out_of_the_queue_as_it_is_out_of_the_list(
    client, session, tmp_path
) -> None:
    """A row's count and the findings list behind it have to agree."""
    repo_id = await _repo(
        client,
        session,
        tmp_path,
        [_metric("a.py", 4.0)],
        [_finding("a.py"), _finding("a.py", "io_in_loop", impact=0.0, dimension="performance")],
    )

    body = await _queue(client, repo_id)

    assert body["finding_total"] == 1
    assert body["targets"][0]["finding_count"] == 1
    assert (await _queue(client, repo_id, "dimension=performance"))["finding_total"] == 1


async def test_a_dismissed_finding_stops_ranking_the_file_as_work(
    client, session, tmp_path
) -> None:
    """Dismissing findings must move a file down the queue, not leave it.

    ``score`` on the row comes from open findings, so impact summed over
    dismissed ones would rank a file by work nobody intends to do.
    """
    repo_id = await _repo(
        client,
        session,
        tmp_path,
        [_metric("a.py", 4.0)],
        [_finding("a.py", impact=3.0), _finding("a.py", "god_class", impact=2.0)],
    )
    findings = (await client.get(f"/api/repos/{repo_id}/health/findings")).json()
    await client.patch(
        f"/api/repos/{repo_id}/health/findings/{findings[0]['id']}",
        json={"status": "false_positive"},
    )

    body = await _queue(client, repo_id, "status=all")
    target = body["targets"][0]

    assert target["finding_count"] == 2
    assert target["open_finding_count"] == 1
    assert target["total_impact"] == 2.0


async def test_an_unknown_status_is_refused_rather_than_answered_emptily(
    client, session, tmp_path
) -> None:
    """``200 []`` for a typo is indistinguishable from "nothing matches"."""
    repo_id = await _repo(client, session, tmp_path, [_metric("a.py", 4.0)], [_finding("a.py")])

    url = f"/api/repos/{repo_id}/health/refactoring-targets?status=dismissed"
    assert (await client.get(url)).status_code == 400

    findings_url = f"/api/repos/{repo_id}/health/findings?status=dismissed"
    assert (await client.get(findings_url)).status_code == 400


async def test_a_rows_count_matches_the_findings_behind_it(client, session, tmp_path) -> None:
    """The expander says "all N findings"; opening it must show N.

    The row's count is computed over the filtered findings, so the per-file
    read behind it has to accept the same filters or the label lies.
    """
    repo_id = await _repo(
        client,
        session,
        tmp_path,
        [_metric("a.py", 4.0)],
        [
            _finding("a.py", severity="critical"),
            _finding("a.py", "god_class", severity="low"),
            _finding("a.py", "large_method", severity="low"),
        ],
    )

    row = (await _queue(client, repo_id, "severity=critical"))["targets"][0]
    behind = (
        await client.get(
            f"/api/repos/{repo_id}/health/findings?file_path=a.py&severity=critical"
        )
    ).json()

    assert row["finding_count"] == len(behind) == 1


async def test_exact_severity_beats_the_threshold_on_the_findings_list(
    client, session, tmp_path
) -> None:
    repo_id = await _repo(
        client,
        session,
        tmp_path,
        [_metric("a.py", 4.0)],
        [_finding("a.py", severity="low"), _finding("a.py", "god_class", severity="critical")],
    )

    url = f"/api/repos/{repo_id}/health/findings?file_path=a.py"
    both = (await client.get(f"{url}&severity=low&min_severity=critical")).json()

    assert [f["severity"] for f in both] == ["low"]
